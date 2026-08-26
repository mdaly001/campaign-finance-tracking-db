"""Generic upsert_records using PostgreSQL INSERT ... ON CONFLICT ... DO UPDATE.

Rows are grouped into multi-row VALUES statements (one INSERT per chunk)
for performance. When no conflict columns are supplied, plain multi-row
INSERTs are used (append-only / surrogate-PK tables).
"""

from typing import Any

from sqlalchemy import text

# Rows per INSERT statement (bounds SQL size for wide VARCHAR rows)
_ROWS_PER_STATEMENT = 500


def upsert_records(
    session,
    table_name: str,
    records: list[dict[str, Any]],
    conflict_columns: list[str],
    batch_size: int = 1000,
) -> int:
    """Insert or update records using PostgreSQL ``INSERT ... ON CONFLICT``.

    With conflict columns: ``ON CONFLICT (...) DO UPDATE SET ...`` (idempotent
    upsert). Without conflict columns: plain multi-row INSERT (append-only).

    Args:
        session: SQLAlchemy ``Engine``, ``Connection`` or ``Session``.
        table_name: Target table name.
        records: List of row dicts to upsert (all must have the same keys).
        conflict_columns: Column names that form the conflict key, or an
            empty list for a plain insert.
        batch_size: Rows per logical batch (each batch is executed as one
            or more multi-row INSERT statements).

    Returns:
        Total number of rows written.
    """
    if not records:
        return 0

    table = _normalize(table_name)
    conflict_cols = [_normalize(c) for c in conflict_columns]

    columns = list(records[0].keys())
    insert_cols = [_normalize(c) for c in columns]

    # Build the ON CONFLICT suffix (or empty for plain inserts)
    if conflict_cols:
        quoted_conflict = ", ".join(f'"{c}"' for c in conflict_cols)
        update_cols = [c for c in insert_cols if c not in conflict_cols]
        if update_cols:
            update_clause = ", ".join(f'{c} = EXCLUDED."{c}"' for c in update_cols)
            suffix = f"ON CONFLICT ({quoted_conflict}) DO UPDATE SET {update_clause}"
        else:
            suffix = f"ON CONFLICT ({quoted_conflict}) DO NOTHING"
    else:
        suffix = ""

    conn = session if hasattr(session, "execute") else session.connect()
    is_managed = session is not conn

    total = 0
    try:
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            total += _execute_batch(conn, table, insert_cols, batch, suffix)
    finally:
        if is_managed:
            conn.close()

    return total


# -- internal helpers ------------------------------------------------------ #


def _normalize(name: str) -> str:
    """Normalize a column/table name for Postgres."""
    return name


def _execute_batch(
    conn,
    table: str,
    columns: list[str],
    batch: list[dict[str, Any]],
    suffix: str,
) -> int:
    """Execute rows as one or more multi-row INSERT statements."""
    col_list = ", ".join(f'"{c}"' for c in columns)
    rows_written = 0

    for start in range(0, len(batch), _ROWS_PER_STATEMENT):
        chunk = batch[start : start + _ROWS_PER_STATEMENT]
        tuples: list[str] = []
        params: dict[str, Any] = {}
        for row_idx, row in enumerate(chunk):
            col_params = []
            for col_idx, col in enumerate(columns):
                param_name = f"v{col_idx}_{start + row_idx}"
                params[param_name] = row.get(col)
                col_params.append(f":{param_name}")
            tuples.append("(" + ", ".join(col_params) + ")")

        sql = f'INSERT INTO "{table}" ({col_list}) VALUES ' + ", ".join(tuples)
        if suffix:
            sql += " " + suffix
        conn.execute(text(sql), params)
        rows_written += len(chunk)

    return rows_written
