"""LoadCheckpoint: idempotent load tracking in Postgres."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text


class LoadCheckpoint:
    """Manages load checkpoints in Postgres ``load_checkpoint`` table.

    Supports resume by table+source+hash so that interrupted or re-run
    jobs can skip already-processed files.

    Expected table schema (created by ``migrations/0001_create_all_tables.sql``)::

        CREATE TABLE load_checkpoint (
            checkpoint_id  SERIAL PRIMARY KEY,
            table_name     VARCHAR(50) NOT NULL,
            source         VARCHAR(30) NOT NULL DEFAULT 'calaccess',
            file_hash      VARCHAR(64) NOT NULL,   -- SHA-256 of the file
            source_file    VARCHAR(200),           -- e.g., 'CalAccess/DATA/RCPT_CD.TSV'
            processed_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rows_processed INTEGER,
            notes          TEXT
        );
        CREATE UNIQUE INDEX idx_load_checkpoint_table_hash
            ON load_checkpoint(table_name, source, file_hash);
    """

    def __init__(self, engine: Any) -> None:
        """
        Args:
            engine: SQLAlchemy ``Engine`` or ``Connection``.
        """
        self._conn = engine

    # -- mutations --------------------------------------------------------- #

    def load_checkpoint(
        self,
        table_name: str,
        file_hash: str,
        processed_date: str | datetime | None = None,
        *,
        source: str = "calaccess",
        source_file: str | None = None,
        rows_processed: int | None = None,
        notes: str | None = None,
    ) -> None:
        """Save checkpoint after processing a file.

        Uses ``ON CONFLICT DO NOTHING`` so repeated calls are safe.

        Args:
            table_name: Target table name (or migration filename).
            file_hash: SHA-256 hex digest of the source file.
            processed_date: Load timestamp. Defaults to ``now(UTC)``.
            source: Data source tag (matches the unique index).
            source_file: Optional source filename, e.g. 'CalAccess/DATA/RCPT_CD.TSV'.
            rows_processed: Optional row count for this load.
            notes: Optional free-form note.
        """
        if processed_date is None:
            processed_date = datetime.now(UTC)
        stmt = text(
            """
            INSERT INTO load_checkpoint (
                table_name, source, file_hash, source_file,
                processed_date, rows_processed, notes
            )
            VALUES (
                :table_name, :source, :file_hash, :source_file,
                :processed_date, :rows_processed, :notes
            )
            ON CONFLICT (table_name, source, file_hash) DO NOTHING
            """
        )
        with self._conn.begin() as conn:
            conn.execute(
                stmt,
                {
                    "table_name": table_name,
                    "source": source,
                    "file_hash": file_hash,
                    "source_file": source_file,
                    "processed_date": processed_date,
                    "rows_processed": rows_processed,
                    "notes": notes,
                },
            )

    # -- queries ----------------------------------------------------------- #

    def get_checkpoint(self, table_name: str, file_hash: str) -> str | None:
        """Return last_processed_date for a loaded file, or None.

        Compares ``table_name`` case-insensitively (the loader stores the
        lower-cased target table name while callers may query with the
        upper-case table code) and the exact ``file_hash``.

        Args:
            table_name: Target table name (case-insensitive).
            file_hash: SHA-256 hex digest of the source file.
        """
        stmt = text(
            "SELECT processed_date FROM load_checkpoint "
            "WHERE LOWER(table_name) = LOWER(:table_name) AND file_hash = :file_hash "
            "ORDER BY checkpoint_id DESC LIMIT 1"
        )
        with self._conn.begin() as conn:
            row = conn.execute(
                stmt,
                {"table_name": table_name, "file_hash": file_hash},
            ).fetchone()
            return row[0] if row else None

    def get_unchecked_tables(self, file_hash: str) -> list[str]:
        """Return list of tables not yet loaded for this file hash.

        This is useful for a "re-ingest" scenario: after a new data file
        arrives (new hash), return every table that has not yet been loaded
        for that hash.

        Args:
            file_hash: The new file's SHA-256 hex digest.
        """
        stmt = text(
            "SELECT DISTINCT table_name FROM load_checkpoint "
            "WHERE file_hash != :file_hash OR file_hash IS NULL"
        )
        with self._conn.begin() as conn:
            rows = conn.execute(
                stmt, {"file_hash": file_hash}
            ).fetchall()
            return [r[0] for r in rows]

    def get_last_date(self, table_name: str) -> str | None:
        """Return the latest processed_date for a table, or None.

        Useful for incremental loaders to know where to resume. Case-
        insensitive in ``table_name`` (stores lower-case, callers may pass
        the upper-case table code).
        """
        stmt = text(
            "SELECT MAX(processed_date) FROM load_checkpoint "
            "WHERE LOWER(table_name) = LOWER(:table_name)"
        )
        with self._conn.begin() as conn:
            row = conn.execute(
                stmt, {"table_name": table_name}
            ).fetchone()
            return row[0] if row and row[0] else None

    def is_loaded(self, table_name: str, file_hash: str) -> bool:
        """Return True if the file has already been loaded for this table."""
        return self.get_checkpoint(table_name, file_hash) is not None
