"""LoadCheckpoint: idempotent load tracking in Postgres."""

from typing import Any

from sqlalchemy import text


class LoadCheckpoint:
    """Manages load checkpoints in Postgres ``load_checkpoint`` table.

    Supports resume by table+date+hash so that interrupted or re-run
    jobs can skip already-processed files.

    Expected table schema (created by ``core/schema/003_load_checkpoint.sql``)::

        CREATE TABLE load_checkpoint (
            id            SERIAL PRIMARY KEY,
            table_name    TEXT NOT NULL,
            file_hash     TEXT NOT NULL,
            processed_date TEXT NOT NULL,
            loaded_at     TIMESTAMPTZ DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_load_checkpoint ON load_checkpoint(table_name, file_hash);
    """

    def __init__(self, engine: Any) -> None:
        """
        Args:
            engine: SQLAlchemy ``Engine`` or ``Connection``.
        """
        self._conn = engine

    # -- mutations --------------------------------------------------------- #

    def load_checkpoint(
        self, table_name: str, file_hash: str, processed_date: str
    ) -> None:
        """Save checkpoint after processing a file.

        Uses ``ON CONFLICT DO NOTHING`` so repeated calls are safe.

        Args:
            table_name: Target table name.
            file_hash: SHA-256 hex digest of the source file.
            processed_date: The data date that was just loaded.
        """
        stmt = text(
            """
            INSERT INTO load_checkpoint (table_name, file_hash, processed_date)
            VALUES (:table_name, :file_hash, :processed_date)
            ON CONFLICT (table_name, file_hash) DO NOTHING
            """
        )
        self._conn.execute(
            stmt,
            {
                "table_name": table_name,
                "file_hash": file_hash,
                "processed_date": processed_date,
            },
        )

    # -- queries ----------------------------------------------------------- #

    def get_checkpoint(self, table_name: str, file_hash: str) -> str | None:
        """Return last_processed_date if checkpoint exists, else None.

        Args:
            table_name: Target table name.
            file_hash: SHA-256 hex digest of the source file.
        """
        stmt = text(
            "SELECT processed_date FROM load_checkpoint "
            "WHERE table_name = :table_name AND file_hash = :file_hash "
            "ORDER BY loaded_at DESC LIMIT 1"
        )
        row = self._conn.execute(
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
        rows = self._conn.execute(
            stmt, {"file_hash": file_hash}
        ).fetchall()
        return [r[0] for r in rows]

    def get_last_date(self, table_name: str) -> str | None:
        """Return the latest processed_date for a table, or None.

        Useful for incremental loaders to know where to resume.
        """
        stmt = text(
            "SELECT MAX(processed_date) FROM load_checkpoint "
            "WHERE table_name = :table_name"
        )
        row = self._conn.execute(
            stmt, {"table_name": table_name}
        ).fetchone()
        return row[0] if row and row[0] else None

    def is_loaded(self, table_name: str, file_hash: str) -> bool:
        """Return True if the file has already been loaded for this table."""
        return self.get_checkpoint(table_name, file_hash) is not None
