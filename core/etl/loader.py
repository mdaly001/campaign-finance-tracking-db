"""Generic TSV-to-Postgres loader with checkpointing, type coercion, and batching."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from core.etl.adapter import LoadSummary
from core.etl.checkpoint import LoadCheckpoint
from core.etl.dead_letter import DeadLetter
from core.etl.tsv import TSVReader
from core.etl.upsert import upsert_records

logger = logging.getLogger(__name__)


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
        - 'numeric'  -> float
        - 'date'     -> datetime.date
        - 'timestamp' -> datetime.datetime
        - 'integer'  -> int
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
                    coerced[col] = float(val)
                elif type_name == "date":
                    coerced[col] = datetime.strptime(str(val), "%Y-%m-%d").date()
                elif type_name == "timestamp":
                    coerced[col] = datetime.strptime(
                        str(val), "%Y-%m-%d %H:%M:%S"
                    )
                elif type_name == "integer":
                    coerced[col] = int(float(val))
                else:
                    logger.warning(
                        "Unknown coercion type '%s' for %s.%s",
                        type_name,
                        self.current_table,
                        col,
                    )
            except (ValueError, TypeError) as e:
                logger.warning(
                    "Type coercion failed for %s.%s = %r: %s",
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
            summary.rows_skipped = len(list(self.reader.read_bytes(raw_bytes)))
            return summary

        # Parse TSV
        records = list(self.reader.read_bytes(raw_bytes))
        summary.rows_read = len(records)
        logger.info("Parsed %d rows for %s", summary.rows_read, config.table_name)

        if not records:
            logger.warning("No records found in %s", config.table_name)
            return summary

        # Add source metadata to each record (stripped before upsert)
        for record in records:
            record["__table__"] = config.table_name
            record["__file_hash__"] = file_hash

        # Process records: coerce, validate, strip columns, batch upsert
        batch: list[dict] = []
        for record in records:
            # Strip unwanted columns
            record = self._strip_columns(record, config.skip_columns)

            # Type coercion
            record = self._coerce_types(record, config.type_coercions)

            # Validate
            if not self._validate_row(record, config):
                summary.rows_skipped += 1
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
            logger.error("Upsert failed for %s: %s", config.table_name, e)

            # Quarantine bad batch to dead letter
            for record in batch:
                self.dead_letter.quarantine(
                    config.table_name,
                    record,
                    str(e),
                    config.tsv_files[0] if config.tsv_files else "unknown",
                )

            summary.rows_failed += len(batch)


def get_load_configs() -> dict[str, LoadConfig]:
    """Return load configurations for all CAL-ACCESS tables.

    This is the central registry that maps table codes to their
    schema details. Populated as we discover each table.

    TODO: Populate this during Step 5 when we discover all tables.
    For now, return an empty dict.
    """
    return {}
