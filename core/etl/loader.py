"""Generic TSV-to-Postgres loader with checkpointing, type coercion, and batching."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Engine, text

from core.etl.adapter import LoadSummary
from core.etl.checkpoint import LoadCheckpoint
from core.etl.dead_letter import DeadLetter
from core.etl.tsv import TSVReader
from core.etl.upsert import upsert_records

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Amendment-version dedup (migration 0004)
# ------------------------------------------------------------------ #
# CAL-ACCESS fact tables store every amendment of a filing as a separate
# row: the composite key is (amend_id, filing_id, form_type, line_item,
# rec_type), and a logical transaction can appear 2-10 times with
# increasing amend_id (0 = original, 1 = first amendment, ...).
#
# Loader invariant: fact tables are ALWAYS loaded into the RAW base tables
# (rcpt_cd, expn_cd, ...) keeping every amendment version, using the full
# composite key (including amend_id) as the upsert conflict key. The
# loader must never collapse amendment versions on write — amendments
# re-filed after the initial load land as new rows with amend_id > 0 and
# supersede the earlier version at query time, not on write.
#
# The query surface must read from the `*_deduped` views (migration
# 0004_dedup_views.sql), which keep only the latest amend_id per
# (filing_id, line_item) group. Use dedup_view_name() to map a base
# table to its deduped view.
DEDUP_FACT_TABLES: tuple[str, ...] = (
    "rcpt_cd",
    "expn_cd",
    "lexp_cd",
    "s497_cd",
    "s498_cd",
    "s496_cd",
    "loan_cd",
    "debt_cd",
    "splt_cd",
    "text_memo_cd",
)


def dedup_view_name(table_name: str) -> str | None:
    """Return the deduped view name for a fact table, or None.

    Every CAL-ACCESS fact table (amendment versioning applies) has a
    companion view `<table>_deduped` created by migration 0004 that
    collapses amendment versions: it keeps only the row with the highest
    amend_id per (filing_id, line_item) group. Tools and reports MUST
    query the deduped view — summing amounts on the raw base table
    double-counts every amended transaction.
    """
    base = table_name.lower()
    if base in DEDUP_FACT_TABLES:
        return f"{base}_deduped"
    return None


_DT_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",   # 1/27/2000 12:00:00 AM  (CAL-ACCESS)
    "%m/%d/%Y %I:%M %p",      # 1/27/2000 12:00 AM
    "%m/%d/%Y %H:%M:%S",      # 01/27/2000 00:00:00
    "%Y-%m-%d %H:%M:%S",      # ISO with time
    "%Y-%m-%dT%H:%M:%S",      # ISO T-separated
    "%m/%d/%Y",               # 1/27/2000 (CAL-ACCESS date)
    "%Y-%m-%d",               # ISO date
)


def _parse_datetime(val: str) -> datetime:
    """Parse a CAL-ACCESS or ISO date/datetime string."""
    val = val.strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognized datetime format: {val!r}")


def _parse_date(val: str) -> date:
    """Parse a CAL-ACCESS or ISO date string (time component ignored)."""
    return _parse_datetime(val).date()


@dataclass
class LoadConfig:
    """Configuration for a single table load operation."""

    table_name: str
    tsv_files: list[str]  # filenames to load (e.g., ["RCPT_CD.tsv"])
    conflict_columns: list[str]  # columns for INSERT ... ON CONFLICT
    type_coercions: dict[str, str] | None = None  # col -> "numeric", "date", ...
    required_columns: list[str] | None = None  # columns that must be present
    skip_columns: list[str] | None = None  # columns to exclude from load
    row_filter: Callable[[dict], bool] | None = None  # function to filter rows


class TableLoader:
    """Generic loader: TSV -> type coercion -> validation -> upsert.

    Handles:
    - TSV parsing via TSVReader
    - Type coercion (string->numeric, string->date, etc.)
    - Required column validation
    - Batch upserts with configurable batch size
    - Checkpoint tracking (resume on failure)
    - Dead-letter quarantine for bad rows
    - Progress logging per batch
    """

    def __init__(self, engine: Engine, batch_size: int = 1000):
        """Initialize with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for Postgres (or SQLite for tests).
            batch_size: Number of rows per batch upsert.
        """
        self.engine = engine
        self.batch_size = batch_size
        self.checkpoint = LoadCheckpoint(engine)
        self.dead_letter = DeadLetter(engine)
        self.reader = TSVReader(has_header=True, empty_to_none=True)

    def _coerce_types(
        self, record: dict, coercions: dict[str, str] | None
    ) -> dict[str, Any]:
        """Apply type coercion to a record.

        Supported types:
        - 'numeric'   -> Decimal (exact money)
        - 'date'      -> datetime.date
        - 'timestamp' -> datetime.datetime
        - 'integer'   -> int

        Date/timestamp values accept the CAL-ACCESS export formats
        (``M/D/YYYY`` with optional ``h:mm:ss AM/PM``) as well as ISO.
        """
        if not coercions:
            return record

        coerced = dict(record)
        for col, type_name in coercions.items():
            if col not in coerced:
                continue
            val = coerced[col]
            if val is None:
                continue

            try:
                if type_name == "numeric":
                    coerced[col] = Decimal(str(val).strip())
                elif type_name == "date":
                    coerced[col] = _parse_date(str(val))
                elif type_name == "timestamp":
                    coerced[col] = _parse_datetime(str(val))
                elif type_name == "integer":
                    coerced[col] = int(float(val))
                else:
                    logger.warning(
                        "Unknown coercion type '%s' for %s.%s",
                        type_name,
                        self.current_table,
                        col,
                    )
            # InvalidOperation: Decimal("garbage"); OverflowError:
            # int(float("1e400")). Both must null out, not crash the load.
            except (ValueError, TypeError, InvalidOperation, OverflowError) as e:
                logger.warning(
                    "Type coercion failed for %s.%s = %r: %s (%s)",
                    type_name,
                    self.current_table,
                    col,
                    val,
                    e,
                )
                coerced[col] = None  # null out on coercion failure

        return coerced

    def _validate_row(self, record: dict, config: LoadConfig) -> bool:
        """Validate a single row against the load config."""
        if config.required_columns:
            for col in config.required_columns:
                if col not in record or record[col] is None:
                    return False

        if config.row_filter and not config.row_filter(record):
            return False

        return True

    def _strip_columns(self, record: dict, skip_columns: list[str] | None) -> dict:
        """Remove skip columns and internal metadata from record."""
        skip = set(skip_columns or [])
        skip.add("__table__")
        skip.add("__file_hash__")
        return {k: v for k, v in record.items() if k not in skip}

    def load(
        self,
        config: LoadConfig,
        raw_bytes: bytes,
    ) -> LoadSummary:
        """Load a single TSV file into a table.

        Args:
            config: Load configuration for the target table.
            raw_bytes: Raw TSV bytes to load.

        Returns:
            LoadSummary with rows_read, rows_upserted, rows_skipped, rows_failed.
        """
        self.current_table = config.table_name

        summary = LoadSummary()

        # Checkpoint: has this file already been loaded?
        file_hash = self._compute_hash(raw_bytes)
        checkpoint_date = self.checkpoint.get_checkpoint(config.table_name, file_hash)

        if checkpoint_date:
            logger.info(
                "Skipping %s (already loaded for file hash %s, checkpoint: %s)",
                config.table_name,
                file_hash[:12],
                checkpoint_date,
            )
            # Cheap row count — avoid re-parsing a large file just to count.
            summary.rows_skipped = max(raw_bytes.count(b"\n") - 1, 0)
            return summary

        # Stream-parse the TSV (normalize column keys to lowercase: TSV
        # headers are upper-case, the DDL uses lower-case identifiers).
        # Streaming keeps peak memory at O(batch_size): one parsed row is
        # live at a time, batches are upserted and discarded — the full
        # file is never materialized as a list of dicts.
        stream = iter(
            (
                {str(k).lower(): v for k, v in rec.items()}
                for rec in self.reader.stream_bytes(raw_bytes)
            )
        )

        first = next(stream, None)
        if first is None:
            logger.warning("No records found in %s", config.table_name)
            return summary

        # Surrogate-PK tables (no conflict key) are loaded append-only:
        # truncate first so each load replaces the full snapshot.
        if not config.conflict_columns:
            with self.engine.begin() as conn:
                conn.execute(text(f'TRUNCATE TABLE "{config.table_name}"'))
            logger.info("Truncated %s (append-only load)", config.table_name)

        # Process records: tag metadata, coerce, validate, strip, batch upsert
        batch: list[dict] = []
        record = first
        while record is not None:
            summary.rows_read += 1

            # Source metadata attached on ingest (stripped again before
            # upsert; kept in the dict so row-level hooks can observe it).
            record["__table__"] = config.table_name
            record["__file_hash__"] = file_hash

            # Strip unwanted columns
            record = self._strip_columns(record, config.skip_columns)

            # Type coercion
            record = self._coerce_types(record, config.type_coercions)

            # Validate
            if not self._validate_row(record, config):
                summary.rows_skipped += 1
                record = next(stream, None)
                continue

            batch.append(record)

            # Batch upsert
            if len(batch) >= self.batch_size:
                self._upsert_batch(config, batch, summary)
                logger.info(
                    "Loaded %d rows into %s (progress: %d)",
                    self.batch_size,
                    config.table_name,
                    summary.rows_upserted + summary.rows_failed,
                )
                batch = []

            record = next(stream, None)

        # Final batch
        if batch:
            self._upsert_batch(config, batch, summary)
            logger.info(
                "Loaded %d rows into %s (final batch)",
                len(batch),
                config.table_name,
            )

        # Save checkpoint
        if summary.rows_upserted > 0 or summary.rows_skipped > 0:
            self.checkpoint.load_checkpoint(
                config.table_name,
                file_hash,
                datetime.now(UTC).isoformat(),
            )

        logger.info(
            "Load complete for %s: %d read, %d upserted, %d skipped, %d failed",
            config.table_name,
            summary.rows_read,
            summary.rows_upserted,
            summary.rows_skipped,
            summary.rows_failed,
        )

        return summary

    def _compute_hash(self, raw_bytes: bytes) -> str:
        """Compute SHA-256 hash of raw bytes."""
        return hashlib.sha256(raw_bytes).hexdigest()

    def _upsert_batch(
        self,
        config: LoadConfig,
        batch: list[dict],
        summary: LoadSummary,
    ) -> None:
        """Upsert a single batch of records.

        Args:
            config: Load configuration.
            batch: Records to upsert.
            summary: Accumulates counts (rows_failed updated on error).
        """
        try:
            with self.engine.begin() as conn:
                upserted = upsert_records(
                    conn,
                    config.table_name,
                    batch,
                    config.conflict_columns,
                    batch_size=1000,
                )
            summary.rows_upserted += upserted
        except Exception as e:
            # A multi-row VALUES statement is all-or-nothing: one bad row
            # (e.g. a blank PK value) would otherwise dead-letter up to a
            # full batch of good rows. Retry row-by-row so that only the
            # genuinely bad rows are quarantined.
            logger.warning(
                "Batch upsert failed for %s (%d rows): %s — retrying row-by-row",
                config.table_name,
                len(batch),
                e,
            )
            recovered = 0
            for record in batch:
                try:
                    with self.engine.begin() as conn:
                        upsert_records(
                            conn,
                            config.table_name,
                            [record],
                            config.conflict_columns,
                        )
                    summary.rows_upserted += 1
                    recovered += 1
                except Exception as row_err:
                    summary.rows_failed += 1
                    # Quarantine the bad row. A dead-letter failure must
                    # never take down the table load — log and continue.
                    try:
                        self.dead_letter.quarantine(
                            config.table_name,
                            record,
                            str(row_err),
                            config.tsv_files[0] if config.tsv_files else "unknown",
                        )
                    except Exception as dl_err:  # noqa: BLE001 - best-effort
                        logger.error(
                            "Dead-letter quarantine failed for %s: %s",
                            config.table_name,
                            dl_err,
                        )
            if recovered:
                logger.info(
                    "Recovered %d/%d rows for %s via row-by-row retry",
                    recovered,
                    len(batch),
                    config.table_name,
                )


def get_load_configs() -> dict[str, LoadConfig]:
    """Return load configurations for all CAL-ACCESS tables.

    This is the central registry that maps table codes to their
    schema details. Populated as we discover each table.

    TODO: Populate this during Step 5 when we discover all tables.
    For now, return an empty dict.
    """
    return {}
