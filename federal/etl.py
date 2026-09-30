"""FEC federal data ETL runner (full / incremental / resume).

Usage:
    python -m federal.etl full --cycles 2024,2026
    python -m federal.etl incremental --cycles 2026
    python -m federal.etl resume
    python -m federal.etl list
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine

from core.etl.adapter import SourceFileInfo
from core.etl.checkpoint import LoadCheckpoint
from core.etl.loader import LoadConfig, TableLoader
from core.etl.logging import setup_logging
from federal.adapter import FECSourceAdapter
from federal.tables import FEC_TABLE_DEFINITIONS, LOAD_ORDER

logger = logging.getLogger(__name__)


def _get_cycles() -> list[int]:
    """Get configured FEC cycles."""
    cycles_env = os.getenv("FEC_CYCLES", "2024,2026")
    try:
        return [int(c.strip()) for c in cycles_env.split(",") if c.strip()]
    except ValueError:
        logger.warning("Invalid FEC_CYCLES config: %s", cycles_env)
        return [2024, 2026]


def _build_load_config(
    code: str,
    tsv_bytes: bytes,
    file_hash: str,
    cycle: int,
) -> LoadConfig:
    """Build a LoadConfig for a single FEC table."""
    if code not in FEC_TABLE_DEFINITIONS:
        raise KeyError(f"Unknown FEC table code: {code}")

    td = FEC_TABLE_DEFINITIONS[code]
    skip = ["__table__", "__file_hash__"] + (td.skip_columns or [])

    return LoadConfig(
        table_name=td.table_name,
        tsv_files=td.source_file or [],
        conflict_columns=td.conflict_columns or [],
        type_coercions=td.type_coercions,
        required_columns=td.required_columns,
        skip_columns=skip,
        source="fec",
        cycle=cycle if td.cycle_scoped else None,
    )


@dataclass
class FullLoadResult:
    """Summary of a full FEC load run."""

    tables_loaded: int = 0
    tables_skipped: int = 0
    total_rows_read: int = 0
    total_rows_upserted: int = 0
    total_rows_skipped: int = 0
    total_rows_failed: int = 0
    duration_seconds: float = 0.0
    tables: list[dict] | None = None


class FullLoadRunner:
    """Iterate over FEC tables per cycle and load each one."""

    def __init__(
        self,
        database_url: str,
        cache_dir: Path,
        batch_size: int = 1000,
        cycles: list[int] | None = None,
    ):
        self.database_url = database_url
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.cycles = cycles or _get_cycles()
        self.engine = create_engine(database_url, pool_size=5, max_overflow=10)
        self.adapter = FECSourceAdapter(cache_dir=cache_dir)

    def run(self) -> FullLoadResult:
        """Execute a full FEC load across all cycles."""
        start = time.monotonic()
        result = FullLoadResult(tables=[])

        logger.info("Starting FEC full load: cycles %s", self.cycles)

        for cycle in self.cycles:
            logger.info("Loading cycle %d", cycle)
            self._load_cycle(cycle, result)

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "FEC full load complete: %d loaded, %d skipped, %.1fs total",
            result.tables_loaded,
            result.tables_skipped,
            result.duration_seconds,
        )

        return result

    def _load_cycle(self, cycle: int, result: FullLoadResult) -> None:
        """Load all tables for a single cycle."""
        for code in LOAD_ORDER:
            table_start = time.monotonic()

            td = FEC_TABLE_DEFINITIONS.get(code)
            if not td:
                continue

            # Skip tables not applicable to this cycle
            if not td.cycle_scoped and code not in ("FEC_CM", "FEC_CN", "FEC_CCL"):
                continue

            logger.info("Loading %s for cycle %d", code, cycle)
            try:
                summary = self._load_table(code, cycle)
            except Exception as e:
                logger.error("Table %s cycle %d failed: %s", code, cycle, e)
                result.tables.append({
                    "code": code,
                    "cycle": cycle,
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
                "cycle": cycle,
                "status": "loaded",
                "rows_read": summary.rows_read,
                "rows_upserted": summary.rows_upserted,
                "rows_skipped": summary.rows_skipped,
                "duration_seconds": round(elapsed, 2),
            })

            logger.info(
                "Table %s cycle %d: %d read, %d upserted, %.1fs",
                code, cycle, summary.rows_read, summary.rows_upserted, elapsed,
            )

    def _load_table(self, code: str, cycle: int) -> any:
        """Load a single FEC table for a cycle."""
        td = FEC_TABLE_DEFINITIONS[code]

        # Download the file
        info = SourceFileInfo(
            name=td.source_file.format(yy=cycle % 100),
            url=f"https://www.fec.gov/files/bulk-downloads/{cycle}/{td.source_file.format(yy=cycle % 100)}",
            zip_member=td.source_member,
        )
        raw_bytes = self.adapter.fetch_file(info)
        file_hash = hashlib.sha256(raw_bytes).hexdigest()

        # Check if already loaded
        checkpoint = LoadCheckpoint(self.engine)
        if checkpoint.is_loaded(td.table_name, file_hash, source="fec"):
            logger.info("Table %s cycle %d already loaded (hash %s)", code, cycle, file_hash[:12])
            from core.etl.adapter import LoadSummary
            return LoadSummary(rows_skipped=max(raw_bytes.count(b"\n") - 1, 0))

        # FEC files are pipe-delimited with no headers.
        # Add a header row with the correct column names.
        header = "|".join(td.column_names)
        raw_with_header = header.encode("utf-8") + b"\n" + raw_bytes

        # Build load config and load with custom reader
        from core.etl.tsv import TSVReader
        fec_reader = TSVReader(has_header=True, delimiter="|", empty_to_none=True)
        config = _build_load_config(code, raw_bytes, file_hash, cycle)
        loader = TableLoader(self.engine, batch_size=self.batch_size, reader=fec_reader)
        summary = loader.load(config, raw_with_header)

        # Checkpoint
        checkpoint.load_checkpoint(
            table_name=td.table_name,
            file_hash=file_hash,
            source="fec",
            source_file=info.name,
            rows_processed=summary.rows_upserted,
            notes=f"cycle {cycle}",
        )

        return summary

    def _inject_cycle(self, table_name: str, cycle: int) -> None:
        """Set two_year_transaction_period for rows loaded with default."""
        from sqlalchemy import text
        schema, tbl = table_name.split(".", 1)
        with self.engine.begin() as conn:
            # Only update rows loaded in the current ETL run (last 2 hours)
            # to avoid migrating rows from previous cycles
            conn.execute(
                text(f'UPDATE "{schema}"."{tbl}" '
                     f'SET two_year_transaction_period = :cycle '
                     f'WHERE two_year_transaction_period = 2024 '
                     f'AND load_ts > now() - interval \'2 hours\' '
                     f'AND :cycle != 2024'),
                {"cycle": cycle},
            )


def main() -> None:
    """CLI entry point for FEC ETL."""
    parser = argparse.ArgumentParser(description="FEC Federal Data ETL")
    subparsers = parser.add_subparsers(dest="command")

    # full
    full_p = subparsers.add_parser("full", help="Full batch load all tables")
    full_p.add_argument("--database-url", default=None)
    full_p.add_argument("--cache-dir", default="/app/federal/cache")
    full_p.add_argument("--batch-size", type=int, default=1000)
    full_p.add_argument("--cycles", default=None, help="Comma-separated cycles (e.g., 2024,2026)")

    # incremental
    inc_p = subparsers.add_parser("incremental", help="Incremental load (changed tables only)")
    inc_p.add_argument("--database-url", default=None)
    inc_p.add_argument("--cache-dir", default="/app/federal/cache")
    inc_p.add_argument("--batch-size", type=int, default=1000)
    inc_p.add_argument("--cycles", default=None)

    # resume
    res_p = subparsers.add_parser("resume", help="Resume from last checkpoint")
    res_p.add_argument("--database-url", default=None)
    res_p.add_argument("--cache-dir", default="/app/federal/cache")
    res_p.add_argument("--batch-size", type=int, default=1000)

    # list
    list_p = subparsers.add_parser("list", help="List all registered FEC tables")

    args = parser.parse_args()
    setup_logging(level="INFO")

    if args.command == "list":
        print("Registered FEC tables (in load order):")
        for i, code in enumerate(LOAD_ORDER, 1):
            td = FEC_TABLE_DEFINITIONS.get(code)
            desc = td.description if td else "(unknown)"
            print(f"  {i:3d}. {code:12s} — {desc}")
        return

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    database_url = args.database_url or os.getenv("DATABASE_URL") or "postgresql://cfdb:cfdb@localhost:5432/cfdb"
    cache_dir = Path(args.cache_dir)

    if args.command == "full":
        cycles = None
        if args.cycles:
            cycles = [int(c.strip()) for c in args.cycles.split(",")]
        runner = FullLoadRunner(
            database_url=database_url,
            cache_dir=cache_dir,
            batch_size=args.batch_size,
            cycles=cycles,
        )
        result = runner.run()
        print(f"\nFEC Full Load: {result.tables_loaded} loaded, {result.tables_skipped} skipped, {result.duration_seconds:.1f}s")
        sys.exit(1 if result.total_rows_failed > 0 else 0)
    else:
        print(f"FEC ETL {args.command} not yet implemented")
        sys.exit(1)


if __name__ == "__main__":
    main()
