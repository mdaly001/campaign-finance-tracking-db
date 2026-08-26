"""Full-history batch load + daily incremental runner.

Usage:
    python -m state.etl --full              Full batch load (all tables)
    python -m state.etl --incremental       Incremental load (changed tables only)
    python -m state.etl --list              List all registered tables
    python -m state.etl --resume            Resume from last checkpoint

Classes:
    FullLoadRunner:     Iterates all tables, loads each, updates checkpoint.
    IncrementalLoadRunner: Checks checksums, loads only changed tables.
    ResumeRunner:       Resumes from last checkpoint on interruption.

Both runners are resumable — if interrupted mid-load, they continue
from the last successfully checkpointed table.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine

from core.etl.checkpoint import LoadCheckpoint
from core.etl.loader import LoadConfig, TableLoader
from core.etl.logging import setup_logging
from state.tables import TABLE_DEFINITIONS

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Load order — dimensions before facts (FK constraints)
# ------------------------------------------------------------------ #
LOAD_ORDER: list[str] = [
    # Dimension / reference tables first (small, FK targets)
    "ACRONYMS_CD",
    "ADDRESS_CD",
    "BALLOT_MEASURES_CD",
    "EFS_FILING_LOG_CD",
    "FILERNAME_CD",
    "FILERS_CD",
    "FILER_ACRONYMS_CD",
    "FILER_ADDRESS_CD",
    "FILER_ETHICS_CLASS_CD",
    "FILER_FILINGS_CD",
    "FILER_INTERESTS_CD",
    "FILER_LINKS_CD",
    "FILER_STATUS_TYPES_CD",
    "FILER_TO_FILER_TYPE_CD",
    "FILER_TYPES_CD",
    "FILER_TYPE_PERIODS_CD",
    "FILER_XREF_CD",
    "FILINGS_CD",
    "FILING_PERIOD_CD",
    "GROUP_TYPES_CD",
    "HEADER_CD",
    "IMAGE_LINKS_CD",
    "LEGISLATIVE_SESSIONS_CD",
    "LOOKUP_CODES_CD",
    "NAMES_CD",
    "RECEIVED_FILINGS_CD",
    "REPORTS_CD",
    # Disclosure forms (CVR series + reports)
    "CVR2_CAMPAIGN_DISCLOSURE_CD",
    "CVR2_LOBBY_DISCLOSURE_CD",
    "CVR2_REGISTRATION_CD",
    "CVR2_SO_CD",
    "CVR3_VERIFICATION_INFO_CD",
    "CVR_CAMPAIGN_DISCLOSURE_CD",
    "CVR_E530_CD",
    "CVR_F470_CD",
    "CVR_LOBBY_DISCLOSURE_CD",
    "CVR_REGISTRATION_CD",
    "CVR_SO_CD",
    # Lobbying tables
    "LATT_CD",
    "LCCM_CD",
    "LEMP_CD",
    "LEXP_CD",
    "LOBBYING_CHG_LOG_CD",
    "LOBBYIST_CONTRIBUTIONS1_CD",
    "LOBBYIST_CONTRIBUTIONS2_CD",
    "LOBBYIST_CONTRIBUTIONS3_CD",
    "LOBBYIST_EMPLOYER1_CD",
    "LOBBYIST_EMPLOYER2_CD",
    "LOBBYIST_EMPLOYER3_CD",
    "LOBBYIST_EMPLOYER_FIRMS1_CD",
    "LOBBYIST_EMPLOYER_FIRMS2_CD",
    "LOBBYIST_EMPLOYER_HISTORY_CD",
    "LOBBYIST_EMP_LOBBYIST1_CD",
    "LOBBYIST_EMP_LOBBYIST2_CD",
    "LOBBYIST_FIRM1_CD",
    "LOBBYIST_FIRM2_CD",
    "LOBBYIST_FIRM3_CD",
    "LOBBYIST_FIRM_EMPLOYER1_CD",
    "LOBBYIST_FIRM_EMPLOYER2_CD",
    "LOBBYIST_FIRM_HISTORY_CD",
    "LOBBYIST_FIRM_LOBBYIST1_CD",
    "LOBBYIST_FIRM_LOBBYIST2_CD",
    "LOBBY_AMENDMENTS_CD",
    "LOTH_CD",
    "LPAY_CD",
    # Fact tables (financial detail)
    "DEBT_CD",
    "F495P2_CD",
    "F501_502_CD",
    "F690P2_CD",
    "HDR_CD",
    "LOAN_CD",
    "S401_CD",
    "S496_CD",
    "S497_CD",
    "S498_CD",
    "SPLT_CD",
    "TEXT_MEMO_CD",
    # Largest fact files last (SMRY ~0.5GB, EXPN ~3GB, RCPT ~3.8GB)
    "SMRY_CD",
    "EXPN_CD",
    "RCPT_CD",
    # Scraper-owned tables (non-TSV — populated via state.scrapers)
    "FILING_CALENDAR",
    "ELECTION_RESULTS",
]


# ------------------------------------------------------------------ #
#  Build LoadConfig from TableDefinition
# ------------------------------------------------------------------ #
def _build_load_config(
    code: str,
    tsv_bytes: bytes,
    file_hash: str,
) -> LoadConfig:
    """Build a LoadConfig for a single table from its definition."""
    if code not in TABLE_DEFINITIONS:
        raise KeyError(f"Unknown table code: {code}")

    td = TABLE_DEFINITIONS[code]
    skip = ["__table__", "__file_hash__"] + (td.skip_columns or [])

    return LoadConfig(
        table_name=code.lower(),
        tsv_files=td.tsv_files or [],
        conflict_columns=td.conflict_columns or [],
        type_coercions=td.type_coercions,
        required_columns=td.required_columns,
        skip_columns=skip,
    )


# ------------------------------------------------------------------ #
#  FullLoadRunner
# ------------------------------------------------------------------ #
@dataclass
class FullLoadResult:
    """Summary of a full-load run."""

    tables_loaded: int = 0
    tables_skipped: int = 0
    total_rows_read: int = 0
    total_rows_upserted: int = 0
    total_rows_skipped: int = 0
    total_rows_failed: int = 0
    duration_seconds: float = 0.0
    tables: list[dict] | None = None  # per-table detail


class FullLoadRunner:
    """Iterate over all CAL-ACCESS tables and load each one.

    Flow:
    1. Download latest dbwebexport.zip from SOS
    2. Iterate all registered table codes in LOAD_ORDER
    3. For each table, extract its TSV, build LoadConfig, load into DB
    4. Save checkpoint after each table
    5. If interrupted, resume from last checkpoint

    Watchdog: if a table that *should* have data (during active filing
    periods) yields zero rows, emit a WARNING.
    """

    def __init__(
        self,
        database_url: str,
        cache_dir: Path,
        batch_size: int = 1000,
        watchdog: bool = True,
    ):
        self.database_url = database_url
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.watchdog = watchdog
        self.engine = create_engine(database_url, pool_size=5, max_overflow=10)

    def run(
        self,
        table_order: list[str] | None = None,
        tables_only: list[str] | None = None,
    ) -> FullLoadResult:
        """Execute a full batch load.

        Args:
            table_order: Override the default LOAD_ORDER.
            tables_only: If set, only load these tables (subset).

        Returns:
            FullLoadResult with per-table and aggregate stats.
        """
        start = time.monotonic()
        result = FullLoadResult()
        order = table_order or LOAD_ORDER

        # Filter to only requested tables
        if tables_only:
            order = [t for t in order if t in tables_only]

        logger.info("Starting full load: %d tables", len(order))

        # Get the source archive (reuses the on-disk cache when present;
        # delete the cache or call adapter.refresh() to force a re-download)
        from state.adapter import StateSourceAdapter

        adapter = StateSourceAdapter(cache_dir=self.cache_dir)
        file_infos = adapter.get_source_files()

        if not file_infos:
            logger.error("No source files found — cannot load")
            return result

        # Compute zip-level hash (used for checkpoint)
        zip_hash = adapter._cached_file.checksum if adapter._cached_file else None
        logger.info("Source checksum: %s (zip level)", zip_hash[:12] if zip_hash else "none")

        # Build table → file-info map (metadata only — bytes are fetched
        # per-table just-in-time so memory stays bounded to one table)
        info_by_code: dict[str, "SourceFileInfo"] = {}
        for info in file_infos:
            tsv_code = Path(info.name).stem
            if tsv_code in TABLE_DEFINITIONS:
                info_by_code[tsv_code] = info

        # Load each table
        result.tables = []
        for code in order:
            table_start = time.monotonic()

            info = info_by_code.get(code)
            if info is None:
                logger.warning(
                    "Table %s: TSV not found in source zip — skipping", code
                )
                result.tables.append({
                    "code": code,
                    "status": "skipped",
                    "reason": "tsv_not_found",
                })
                result.tables_skipped += 1
                continue

            tsv_bytes = adapter.fetch_file(info)
            file_hash = hashlib.sha256(tsv_bytes).hexdigest()

            # Build LoadConfig and load
            config = _build_load_config(code, tsv_bytes, file_hash)
            loader = TableLoader(self.engine, batch_size=self.batch_size)

            try:
                summary = loader.load(config, tsv_bytes)
            except Exception as e:
                logger.error("Table %s: load failed — %s", code, e)
                result.tables.append({
                    "code": code,
                    "status": "failed",
                    "error": str(e),
                })
                result.total_rows_failed += 1
                continue

            elapsed = time.monotonic() - table_start
            result.tables_loaded += 1
            result.total_rows_read += summary.rows_read
            result.total_rows_upserted += summary.rows_upserted
            result.total_rows_skipped += summary.rows_skipped

            result.tables.append({
                "code": code,
                "status": "loaded",
                "rows_read": summary.rows_read,
                "rows_upserted": summary.rows_upserted,
                "rows_skipped": summary.rows_skipped,
                "rows_failed": summary.rows_failed,
                "duration_seconds": round(elapsed, 2),
            })

            logger.info(
                "Table %s: %d read, %d upserted, %d skipped, %d failed (%.1fs)",
                code,
                summary.rows_read,
                summary.rows_upserted,
                summary.rows_skipped,
                summary.rows_failed,
                elapsed,
            )

            # Watchdog: zero rows during expected filing periods
            if self.watchdog and summary.rows_read == 0 and summary.rows_skipped == 0:
                logger.warning(
                    "WATCHDOG: Table %s has zero rows — unexpected during active filing period",
                    code,
                )

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "Full load complete: %d loaded, %d skipped, %.1fs total",
            result.tables_loaded,
            result.tables_skipped,
            result.duration_seconds,
        )

        return result


# ------------------------------------------------------------------ #
#  IncrementalLoadRunner
# ------------------------------------------------------------------ #
@dataclass
class IncrementalResult:
    """Summary of an incremental load run."""

    tables_checked: int = 0
    tables_loaded: int = 0
    tables_skipped: int = 0
    total_rows_upserted: int = 0
    total_duration_seconds: float = 0.0
    details: list[dict] | None = None


class IncrementalLoadRunner:
    """Check each table's source checksum and load only changed tables.

    Flow:
    1. Download latest dbwebexport.zip from SOS (if newer than cached)
    2. For each table, compare current TSV checksum to last-known checkpoint
    3. Load only tables whose checksum has changed
    4. Save checkpoint after each loaded table
    5. Resume from last checkpoint if interrupted

    If the zip itself hasn't changed (checksum matches cached), skip all
    tables — no work needed.
    """

    def __init__(
        self,
        database_url: str,
        cache_dir: Path,
        batch_size: int = 1000,
    ):
        self.database_url = database_url
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.engine = create_engine(database_url, pool_size=5, max_overflow=10)

    def run(
        self,
        table_order: list[str] | None = None,
        tables_only: list[str] | None = None,
    ) -> IncrementalResult:
        """Execute an incremental load.

        Returns:
            IncrementalResult with per-table and aggregate stats.
        """
        start = time.monotonic()
        result = IncrementalResult()
        order = table_order or LOAD_ORDER

        if tables_only:
            order = [t for t in order if t in tables_only]

        logger.info("Starting incremental load: %d tables", len(order))

        from state.adapter import StateSourceAdapter

        adapter = StateSourceAdapter(cache_dir=self.cache_dir)

        # Check whether the remote archive changed since the cached copy.
        # If so, refresh; otherwise reuse the cache. The per-file content
        # hash comparison below remains the authoritative load gate.
        try:
            if adapter.is_up_to_date():
                logger.info("Cached dbwebexport.zip is current — reusing it")
            else:
                logger.info("Remote dbwebexport.zip appears newer — downloading")
                adapter.refresh()
        except Exception as e:
            logger.warning(
                "Could not verify zip freshness: %s — using cache as-is", e
            )

        # Build table → file-info map (bytes fetched per-table just-in-time)
        file_infos = adapter.get_source_files()
        info_by_code: dict[str, "SourceFileInfo"] = {}
        for info in file_infos:
            tsv_code = Path(info.name).stem
            if tsv_code in TABLE_DEFINITIONS:
                info_by_code[tsv_code] = info

        # Load each table (only if checksum changed)
        result.details = []
        for code in order:
            result.tables_checked += 1
            table_start = time.monotonic()

            info = info_by_code.get(code)
            if info is None:
                logger.debug("Table %s: TSV not in source — skipping", code)
                result.tables_skipped += 1
                result.details.append({
                    "code": code,
                    "status": "skipped",
                    "reason": "tsv_not_found",
                })
                continue

            tsv_bytes = adapter.fetch_file(info)
            file_hash = hashlib.sha256(tsv_bytes).hexdigest()

            # Check if already loaded
            checkpoint = LoadCheckpoint(self.engine)
            if checkpoint.is_loaded(code, file_hash):
                logger.debug(
                    "Table %s: already loaded (hash %s) — skipping",
                    code,
                    file_hash[:12],
                )
                result.tables_skipped += 1
                result.details.append({
                    "code": code,
                    "status": "skipped",
                    "reason": "already_loaded",
                })
                continue

            # Load this table
            config = _build_load_config(code, tsv_bytes, file_hash)
            loader = TableLoader(self.engine, batch_size=self.batch_size)

            try:
                summary = loader.load(config, tsv_bytes)
            except Exception as e:
                logger.error("Table %s: incremental load failed — %s", code, e)
                result.details.append({
                    "code": code,
                    "status": "failed",
                    "error": str(e),
                })
                continue

            elapsed = time.monotonic() - table_start
            result.tables_loaded += 1
            result.total_rows_upserted += summary.rows_upserted

            result.details.append({
                "code": code,
                "status": "loaded",
                "rows_upserted": summary.rows_upserted,
                "duration_seconds": round(elapsed, 2),
            })

            logger.info(
                "Table %s: %d upserted (%.1fs)",
                code,
                summary.rows_upserted,
                elapsed,
            )

        result.total_duration_seconds = time.monotonic() - start
        logger.info(
            "Incremental load complete: %d checked, %d loaded, %d skipped, %.1fs total",
            result.tables_checked,
            result.tables_loaded,
            result.tables_skipped,
            result.total_duration_seconds,
        )

        return result


# ------------------------------------------------------------------ #
#  ResumeRunner
# ------------------------------------------------------------------ #
class ResumeRunner:
    """Resume a full load from the last checkpoint.

    After a successful run, all tables are checkpointed. After a failed
    run, some tables are checkpointed and others are not. This runner
    loads only the non-checkpointed tables in the correct order.
    """

    def __init__(
        self,
        database_url: str,
        cache_dir: Path,
        batch_size: int = 1000,
    ):
        self.database_url = database_url
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.engine = create_engine(database_url, pool_size=5, max_overflow=10)

    def run(
        self,
        table_order: list[str] | None = None,
    ) -> FullLoadResult:
        """Resume loading from last checkpoint.

        Returns:
            FullLoadResult with stats.
        """
        start = time.monotonic()
        result = FullLoadResult()
        order = table_order or LOAD_ORDER

        logger.info("Resuming load: %d tables", len(order))

        from state.adapter import StateSourceAdapter

        adapter = StateSourceAdapter(cache_dir=self.cache_dir)
        file_infos = adapter.get_source_files()

        # Build table → tsv mapping
        table_tsv_map: dict[str, bytes] = {}
        for info in file_infos:
            tsv_code = Path(info.name).stem
            if tsv_code in TABLE_DEFINITIONS:
                raw = adapter.fetch_file(info)
                table_tsv_map[tsv_code] = raw

        # Find tables not yet loaded for this zip hash
        zip_hash = adapter._cached_file.checksum if adapter._cached_file else None
        checkpoint = LoadCheckpoint(self.engine)
        unchecked = checkpoint.get_unchecked_tables(zip_hash) if zip_hash else order

        # Filter order to only unchecked tables, preserving order
        pending = [t for t in order if t in unchecked]

        if not pending:
            logger.info("All tables already loaded — nothing to resume")
            return result

        logger.info("Resuming %d tables: %s", len(pending), pending[:5])
        if len(pending) > 5:
            logger.info("... and %d more", len(pending) - 5)

        result.tables = []
        for code in pending:
            table_start = time.monotonic()

            if code not in table_tsv_map:
                logger.warning("Table %s: TSV not found — skipping", code)
                result.tables_skipped += 1
                result.tables.append({
                    "code": code,
                    "status": "skipped",
                    "reason": "tsv_not_found",
                })
                continue

            tsv_bytes = table_tsv_map[code]
            file_hash = hashlib.sha256(tsv_bytes).hexdigest()
            config = _build_load_config(code, tsv_bytes, file_hash)
            loader = TableLoader(self.engine, batch_size=self.batch_size)

            try:
                summary = loader.load(config, tsv_bytes)
            except Exception as e:
                logger.error("Table %s: resume failed — %s", code, e)
                result.tables.append({
                    "code": code,
                    "status": "failed",
                    "error": str(e),
                })
                result.total_rows_failed += 1
                continue

            elapsed = time.monotonic() - table_start
            result.tables_loaded += 1
            result.total_rows_read += summary.rows_read
            result.total_rows_upserted += summary.rows_upserted
            result.total_rows_skipped += summary.rows_skipped

            result.tables.append({
                "code": code,
                "status": "loaded",
                "rows_read": summary.rows_read,
                "rows_upserted": summary.rows_upserted,
                "rows_skipped": summary.rows_skipped,
                "duration_seconds": round(elapsed, 2),
            })

            logger.info("Table %s: %d upserted (%.1fs)", code, summary.rows_upserted, elapsed)

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "Resume complete: %d loaded, %.1fs total",
            result.tables_loaded,
            result.duration_seconds,
        )

        return result


# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Campaign Finance DB — load runner (full / incremental / resume)",
    )
    subparsers = parser.add_subparsers(dest="command")

    # -- full --
    full_p = subparsers.add_parser("full", help="Full batch load all tables")
    full_p.add_argument("--database-url", default=None, help="Postgres connection URL")
    full_p.add_argument("--cache-dir", default="/app/state/cache", help="Cache directory for zip")
    full_p.add_argument("--batch-size", type=int, default=1000, help="Rows per batch")
    full_p.add_argument("--no-watchdog", action="store_true", help="Disable zero-rows watchdog")
    full_p.add_argument(
        "--tables", nargs="+", default=None, help="Only load these table codes"
    )
    full_p.add_argument("--log-level", default="INFO")

    # -- incremental --
    inc_p = subparsers.add_parser("incremental", help="Incremental load (changed tables only)")
    inc_p.add_argument("--database-url", default=None, help="Postgres connection URL")
    inc_p.add_argument("--cache-dir", default="/app/state/cache", help="Cache directory for zip")
    inc_p.add_argument("--batch-size", type=int, default=1000, help="Rows per batch")
    inc_p.add_argument(
        "--tables", nargs="+", default=None, help="Only check these table codes"
    )
    inc_p.add_argument("--log-level", default="INFO")

    # -- resume --
    res_p = subparsers.add_parser("resume", help="Resume from last checkpoint")
    res_p.add_argument("--database-url", default=None, help="Postgres connection URL")
    res_p.add_argument("--cache-dir", default="/app/state/cache", help="Cache directory for zip")
    res_p.add_argument("--batch-size", type=int, default=1000, help="Rows per batch")
    res_p.add_argument("--log-level", default="INFO")

    # -- list --
    list_p = subparsers.add_parser("list", help="List all registered tables")
    list_p.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    setup_logging(level=args.log_level)

    if args.command == "list":
        print("Registered tables (in load order):")
        for i, code in enumerate(LOAD_ORDER, 1):
            td = TABLE_DEFINITIONS.get(code)
            desc = td.description if td else "(unknown)"
            print(f"  {i:3d}. {code:25s} — {desc}")
        # Any tables not in LOAD_ORDER
        extra = [c for c in TABLE_DEFINITIONS if c not in LOAD_ORDER]
        if extra:
            print("\nAdditional tables (not in default load order):")
            for code in sorted(extra):
                td = TABLE_DEFINITIONS[code]
                print(f"  {code:25s} — {td.description}")
        return

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    database_url = args.database_url or "postgresql://cfdb:cfdb@localhost:5432/cfdb"
    cache_dir = Path(args.cache_dir)

    if args.command == "full":
        runner = FullLoadRunner(
            database_url=database_url,
            cache_dir=cache_dir,
            batch_size=args.batch_size,
            watchdog=not args.no_watchdog,
        )
        result = runner.run(tables_only=args.tables)
        _print_summary(result)
        sys.exit(1 if result.total_rows_failed > 0 else 0)

    elif args.command == "incremental":
        runner = IncrementalLoadRunner(
            database_url=database_url,
            cache_dir=cache_dir,
            batch_size=args.batch_size,
        )
        result = runner.run(tables_only=args.tables)
        _print_incremental_summary(result)
        failed = sum(
            1 for d in (result.details or []) if d.get("status") == "failed"
        )
        sys.exit(1 if failed > 0 else 0)

    elif args.command == "resume":
        runner = ResumeRunner(
            database_url=database_url,
            cache_dir=cache_dir,
            batch_size=args.batch_size,
        )
        result = runner.run()
        _print_summary(result)
        sys.exit(1 if result.total_rows_failed > 0 else 0)


def _print_summary(result: FullLoadResult) -> None:
    """Print a human-readable full-load summary."""
    print("\n" + "=" * 70)
    print("FULL LOAD SUMMARY")
    print("=" * 70)
    print(f"  Tables loaded:    {result.tables_loaded}")
    print(f"  Tables skipped:   {result.tables_skipped}")
    print(f"  Total rows read:  {result.total_rows_read:,}")
    print(f"  Total rows upserted: {result.total_rows_upserted:,}")
    print(f"  Total rows failed: {result.total_rows_failed}")
    print(f"  Duration:         {result.duration_seconds:.1f}s")
    print("=" * 70)

    if result.tables:
        print("\nPer-table detail:")
        print(
            "  "
            f"{'Table':<25s} "
            f"{'Status':<10s} "
            f"{'Read':>10s} "
            f"{'Upserted':>10s} "
            f"{'Skipped':>10s} "
            f"{'Duration':>10s}"
        )
        print("  " + "-" * 70)
        for t in result.tables:
            code = t.get("code", "?")
            status = t.get("status", "?")
            read = t.get("rows_read", 0)
            upserted = t.get("rows_upserted", 0)
            skipped = t.get("rows_skipped", 0)
            dur = t.get("duration_seconds", 0)
            print(
                f"  {code:<25s} "
                f"{status:<10s} "
                f"{read:>10,} "
                f"{upserted:>10,} "
                f"{skipped:>10,} "
                f"{dur:>9.1f}s"
            )


def _print_incremental_summary(result: IncrementalResult) -> None:
    """Print a human-readable incremental-load summary."""
    print("\n" + "=" * 70)
    print("INCREMENTAL LOAD SUMMARY")
    print("=" * 70)
    print(f"  Tables checked:   {result.tables_checked}")
    print(f"  Tables loaded:    {result.tables_loaded}")
    print(f"  Tables skipped:   {result.tables_skipped}")
    print(f"  Total rows upserted: {result.total_rows_upserted:,}")
    print(f"  Duration:         {result.total_duration_seconds:.1f}s")
    print("=" * 70)

    if result.details:
        print("\nPer-table detail:")
        for d in result.details:
            code = d.get("code", "?")
            status = d.get("status", "?")
            reason = d.get("reason", "")
            if status == "loaded":
                upserted = d.get("rows_upserted", 0)
                print(f"  {code:<25s} LOADED  upserted={upserted:,}")
            else:
                print(f"  {code:<25s} SKIPPED  reason={reason}")


if __name__ == "__main__":
    main()
