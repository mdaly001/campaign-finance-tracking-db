"""Generic upsert_records using PostgreSQL INSERT ... ON CONFLICT ... DO UPDATE."""

from typing import Any

from sqlalchemy import text


def upsert_records(
    session,
    table_name: str,
    records: list[dict[str, Any]],
    conflict_columns: list[str],
    batch_size: int = 1000,
) -> int:
    """Insert or update records using PostgreSQL ``INSERT ... ON CONFLICT ... DO UPDATE``.

    Uses batch inserts for performance. All columns in the records are
    upserted; columns not in the conflict key are updated on conflict.

    Args:
        session: SQLAlchemy ``Engine`` or ``Connection``.
        table_name: Target table name.
        records: List of row dicts to upsert.
        conflict_columns: Column names that form the conflict key
            (e.g. ``["id"]`` or ``["filing_id", "cycle"]``).
        batch_size: Number of rows per batch insert.

    Returns:
        Total number of rows upserted.
    """
    if not records:
        return 0

    table = _normalize(table_name)
    conflict_cols = [_normalize(c) for c in conflict_columns]

    columns = list(records[0].keys())
    insert_cols = [_normalize(c) for c in columns]

    # Exclude conflict columns from the UPDATE set
    update_cols = [c for c in insert_cols if c not in conflict_cols]
    update_clause_parts = []
    for c in update_cols:
        update_clause_parts.append(f'{c} = EXCLUDED."{c}"')
    update_clause = ", ".join(update_clause_parts)

    conflict_clause = ", ".join(f'"{c}"' for c in conflict_cols)

    total = 0
    conn = session if hasattr(session, "execute") else session.connect()
    is_managed = session is not conn

    try:
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start : batch_start + batch_size]
            total += _execute_batch(
                conn,
                table,
                insert_cols,
                batch,
                update_clause,
                conflict_clause,
            )
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
    update_clause: str,
    conflict_clause: str,
) -> int:
    """Execute a single batch of rows using parameterized queries."""
    rows_inserted = 0

    # Build the INSERT template with numbered params per position
    col_list = ", ".join(f'"{c}"' for c in columns)
    for row_idx, row in enumerate(batch):
        # Each column gets a named parameter
        param_dict: dict[str, Any] = {}
        col_params: list[str] = []
        for col_idx, col in enumerate(columns):
            param_name = f"v{col_idx}_{row_idx}"
            param_dict[param_name] = row[col]
            col_params.append(f":{param_name}")

        placeholders = ", ".join(col_params)

        sql = (
            f'INSERT INTO "{table}" ({col_list}) '
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_clause}) DO UPDATE SET {update_clause}"
        )
        conn.execute(text(sql), param_dict)
        rows_inserted += 1

    return rows_inserted
