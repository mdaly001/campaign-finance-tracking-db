"""Migration runner for versioned SQL migrations.

Usage:
    python -m core.migrations.migrate --direction=up
    python -m core.migrations.migrate --direction=down
    python -m core.migrations.migrate --dry-run
"""

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import create_engine

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.etl.checkpoint import LoadCheckpoint
from core.etl.logging import setup_logging

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def get_migration_files() -> list[str]:
    """Return sorted list of migration SQL files."""
    return sorted([
        str(f) for f in MIGRATIONS_DIR.iterdir()
        if f.suffix == ".sql" and f.is_file()
    ])


def compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a migration file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _has_sql_content(stmt: str) -> bool:
    """Return True if *stmt* contains anything besides whitespace and comments.

    A fragment consisting only of ``--`` line comments and/or ``/* */`` block
    comments is not executable SQL (psycopg2 raises "can't execute an empty
    query"), so the splitter uses this to drop such fragments. String literals
    and ``$$ ... $$`` bodies are respected when scanning.
    """
    i = 0
    n = len(stmt)
    in_single_quote = False
    in_dollar_quote = False
    while i < n:
        ch = stmt[i]
        if in_dollar_quote:
            if stmt.startswith("$$", i):
                in_dollar_quote = False
                i += 2
                continue
            i += 1
            continue
        if in_single_quote:
            if ch == "'":
                if i + 1 < n and stmt[i + 1] == "'":
                    i += 2
                    continue
                in_single_quote = False
            i += 1
            continue
        if stmt.startswith("$$", i):
            in_dollar_quote = True
            i += 2
            continue
        if ch == "'":
            in_single_quote = True
            i += 1
            continue
        if ch == "-" and stmt.startswith("--", i):
            while i < n and stmt[i] != "\n":
                i += 1
            continue
        if ch == "/" and stmt.startswith("/*", i):
            i += 2
            while i + 1 < n and not (stmt[i] == "*" and stmt[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if not ch.isspace():
            return True
        i += 1
    return False


def split_sql_statements(sql: str) -> list[str]:
    """Split raw SQL text into individual executable statements.

    Splits on ``;`` while respecting:
      * ``$$ ... $$`` dollar-quoted bodies (plpgsql ``DO``/function blocks),
      * ``'...'`` single-quoted string literals (including ``''`` escapes),
      * ``-- ...`` line comments,
      * ``/* ... */`` block comments (no nesting),

    so that a semicolon inside a string, a plpgsql body, or a comment does
    not terminate the surrounding statement.

    Args:
        sql: Raw SQL text (may span many lines).

    Returns:
        List of non-empty statement strings, in order.
    """
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_dollar_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]
        current.append(ch)

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and i + 1 < n and sql[i + 1] == "/":
                current.append(sql[i + 1])
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_dollar_quote:
            # Consume until the closing $$ delimiter.
            if sql.startswith("$$", i):
                current.append(sql[i + 1])
                in_dollar_quote = False
                i += 2
                continue
            i += 1
            continue

        if in_single_quote:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":  # escaped quote ''
                    current.append(sql[i + 1])
                    i += 2
                    continue
                in_single_quote = False
            i += 1
            continue

        # Outside any quote or comment.
        if sql.startswith("$$", i):
            current.append(sql[i + 1])
            in_dollar_quote = True
            i += 2
            continue

        if ch == "'":
            in_single_quote = True
            i += 1
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            current.append(sql[i + 1])
            in_line_comment = True
            i += 2
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            current.append(sql[i + 1])
            in_block_comment = True
            i += 2
            continue

        if ch == ";":
            stmt = "".join(current).strip()
            if _has_sql_content(stmt):
                statements.append(stmt)
            current = []
            i += 1
            continue

        i += 1

    tail = "".join(current).strip()
    if _has_sql_content(tail):
        statements.append(tail)

    return statements


def _checkpoint_table_exists(conn) -> bool:
    """Return True if the load_checkpoint table exists in the database.

    On a fresh database this table does not exist yet — it is created by the
    first migration itself — so its absence means no migration has been
    applied.
    """
    row = conn.execute(
        text("SELECT to_regclass('load_checkpoint') IS NOT NULL")
    ).scalar()
    return bool(row)


def apply_migration(engine, filepath: str, dry_run: bool = False) -> None:
    """Apply a single migration file."""
    filename = os.path.basename(filepath)
    file_hash = compute_file_hash(filepath)

    # Check if already applied (using migration table checkpoint). Skip the
    # check when the checkpoint table does not exist yet (fresh database).
    checkpoint = LoadCheckpoint(engine=engine)
    with engine.connect() as conn:
        if _checkpoint_table_exists(conn):
            if checkpoint.is_loaded(filename, file_hash):
                logger.info("  ✓ Migration already applied: %s", filename)
                return

    logger.info("Applying migration: %s", filename)

    # Read and split SQL into statements
    with open(filepath) as f:
        sql = f.read()

    statements = split_sql_statements(sql)

    if dry_run:
        logger.info(
            "  [DRY RUN] Would execute %d statements from %s",
            len(statements), filename
        )
        return

    # Execute statements in transaction
    with engine.begin() as conn:
        for i, stmt in enumerate(statements):
            try:
                conn.execute(text(stmt))
                logger.debug("    Statement %d applied", i + 1)
            except Exception as e:
                logger.error("    Statement %d failed: %s", i + 1, e)
                raise

    # Record checkpoint
    checkpoint.load_checkpoint(
        table_name=filename,
        file_hash=file_hash,
        rows_processed=len(statements),
        notes="migration applied",
    )

    logger.info(
        "  ✓ Migration applied: %s (%d statements)",
        filename, len(statements)
    )


def run_migrations(direction: str = "up", dry_run: bool = False) -> None:
    """Run migrations in the specified direction.

    Args:
        direction: 'up' to apply, 'down' to rollback (not yet implemented)
        dry_run: If True, only log what would be done
    """
    from config.settings import get_database_url
    database_url = get_database_url()

    engine = create_engine(database_url)

    logger.info("Migration runner starting")
    logger.info("  Database: %s", database_url.split("@")[-1])
    logger.info("  Direction: %s", direction)
    logger.info("  Migrations dir: %s", MIGRATIONS_DIR)

    if direction == "up":
        migration_files = get_migration_files()
        logger.info("Found %d migration(s)", len(migration_files))

        for filepath in migration_files:
            try:
                apply_migration(engine, filepath, dry_run=dry_run)
            except Exception as e:
                logger.error(
                    "Migration failed: %s - %s",
                    os.path.basename(filepath), e
                )
                raise

    logger.info("Migration runner complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run database migrations")
    parser.add_argument(
        "--direction",
        choices=["up", "down"],
        default="up",
        help="Migration direction (default: up)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be done without executing"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()
    level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=level)

    run_migrations(direction=args.direction, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
