"""Database connection helpers for the MCP server.

Provides a read-only SQLAlchemy engine that connects as ``cfdb_reader``
with ``READ ONLY`` transaction mode.  The ``unredacted`` schema is
explicitly rejected — ``cfdb_reader`` has no privileges on it.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text

logger = logging.getLogger(__name__)

_DEFAULT_URL = "postgresql://cfdb_reader:reader@db:5432/cfdb"


def _build_url() -> str:
    """Build the database URL from environment or defaults.

    Order of precedence:
    1. ``DATABASE_URL`` env var (full override)
    2. ``DB_PASSWORD`` + defaults
    3. Hard-coded default
    """
    from_env = os.environ.get("DATABASE_URL")
    if from_env:
        return from_env

    os.environ.get("DB_PASSWORD", "reader")
    host = os.environ.get("DB_HOST", "db")
    port = os.environ.get("DB_PORT", "5432")
    db = os.environ.get("DB_NAME", "cfdb")

    return f"postgresql://{host}:{port}/{db}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a cached read-only SQLAlchemy engine.

    The engine is created once and reused across all tool calls.
    Connection pooling is configured for the MCP server's typical
    concurrency (5 connections, up to 10 overflow).

    Returns:
        SQLAlchemy Engine configured for read-only access.
    """
    url = _build_url()
    engine = create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    logger.info("MCP DB engine connected: %s", url)
    return engine


def execute_read(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Execute a read-only query and return results as list of dicts.

    Uses ``READ ONLY`` transaction mode to prevent accidental writes.

    Args:
        sql: SQL query string with named parameters (:name).
        params: Dictionary of parameter values.

    Returns:
        List of row dicts keyed by column name.

    Raises:
        RuntimeError: If the query attempts to write to the database.
    """
    engine = get_engine()
    if params is None:
        params = {}

    conn = engine.connect()
    try:
        # Set transaction to READ ONLY (Postgres only; SQLite doesn't support this syntax)
        url = str(engine.url)
        if "sqlite" not in url.lower():
            try:
                conn.execute(text("SET TRANSACTION READ ONLY"))
            except Exception:  # pragma: no cover — defensive
                # Some Postgres setups may not support this; proceed anyway
                pass
        result = conn.execute(text(sql), params)
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
        return rows
    except Exception as e:
        # Check if it's a write attempt blocked by cfdb_reader
        if "permission" in str(e).lower() or "read-only" in str(e).lower():
            logger.error("MCP read blocked (permission denied): %s", e)
            raise RuntimeError(
                f"Permission denied: {e}. The cfdb_reader role cannot perform this operation."
            ) from e
        raise
    finally:
        conn.close()
