"""DeadLetter: quarantine bad rows to etl_dead_letter table."""

import json

from sqlalchemy import text


class DeadLetter:
    """Quarantines bad rows to ``etl_dead_letter`` table.

    When a record fails validation or conversion during ETL, it is
    written here for later review and possible reprocessing.

    Expected table schema (created by ``core/schema/004_etl_dead_letter.sql``)::

        CREATE TABLE etl_dead_letter (
            id              SERIAL PRIMARY KEY,
            table_name      TEXT NOT NULL,
            row_data        JSONB NOT NULL,
            error_message   TEXT NOT NULL,
            source_file     TEXT,
            created_at      TIMESTAMPTZ DEFAULT now()
        );
    """

    def __init__(self, engine) -> None:
        """
        Args:
            engine: SQLAlchemy ``Engine`` or ``Connection``.
        """
        self._conn = engine

    def quarantine(
        self,
        table_name: str,
        row: dict,
        error: str,
        source_file: str,
    ) -> None:
        """Insert a bad row into the dead-letter table.

        Args:
            table_name: The table this row was destined for.
            row: The original (failing) row dict.
            error: Human-readable error description.
            source_file: Source file path or name.
        """
        stmt = text(
            """
            INSERT INTO etl_dead_letter (table_name, row_data, error_message, source_file)
            VALUES (:table_name, :row_data, :error, :source_file)
            """
        )
        # engine.begin() commits — a bare connect() would roll back the
        # INSERT on close, silently losing the audit trail.
        with self._conn.begin() as conn:
            conn.execute(
                stmt,
                {
                    "table_name": table_name,
                    # default=str: coerced records carry Decimal/datetime
                    # values that are not JSON-serializable by default.
                    "row_data": json.dumps(row, default=str),
                    "error": error,
                    "source_file": source_file,
                },
            )

    def get_dead_letters(
        self, table_name: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Retrieve dead-letter entries for review.

        Args:
            table_name: Filter by table name (None = all tables).
            limit: Max rows to return.

        Returns:
            List of dicts with keys: id, table_name, row_data (as dict),
            error_message, source_file, created_at.
        """
        query = (
            "SELECT id, table_name, row_data, error_message, "
            "source_file, created_at FROM etl_dead_letter"
        )
        conditions: list[str] = []
        params: dict = {}
        if table_name:
            conditions.append("table_name = :table_name")
            params["table_name"] = table_name
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit

        with self._conn.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
            result: list[dict] = []
            for row in rows:
                result.append(
                    {
                        "id": row[0],
                        "table_name": row[1],
                        "row_data": json.loads(row[2]) if isinstance(row[2], str) else row[2],
                        "error_message": row[3],
                        "source_file": row[4],
                        "created_at": row[5],
                    }
                )
            return result

    def count_by_table(self, table_name: str) -> int:
        """Return the number of dead-lettered rows for a table."""
        stmt = text(
            "SELECT COUNT(*) FROM etl_dead_letter WHERE table_name = :t"
        )
        with self._conn.connect() as conn:
            row = conn.execute(
                stmt, {"t": table_name}
            ).fetchone()
            return row[0] if row else 0

    def clear_stale(self, table_name: str, older_than_days: int = 30) -> int:
        """Clear dead-letter entries older than N days. Returns rows deleted."""
        stmt = text(
            "DELETE FROM etl_dead_letter "
            "WHERE table_name = :t AND created_at < now() - INTERVAL :days DAY"
        )
        with self._conn.connect() as conn:
            result = conn.execute(
                stmt, {"t": table_name, "days": older_than_days}
            )
            return result.rowcount
