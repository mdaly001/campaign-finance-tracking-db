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


def apply_migration(engine, filepath: str, dry_run: bool = False) -> None:
    """Apply a single migration file."""
    filename = os.path.basename(filepath)
    file_hash = compute_file_hash(filepath)

    # Check if already applied (using migration table checkpoint)
    checkpoint = LoadCheckpoint(engine=engine)
    if checkpoint.is_loaded(filename, file_hash):
        logger.info("  ✓ Migration already applied: %s", filename)
        return

    logger.info("Applying migration: %s", filename)

    # Read and split SQL on semicolons
    with open(filepath) as f:
        sql = f.read()

    statements = []
    current = []
    for line in sql.split("\n"):
        current.append(line)
        if line.strip().endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []

    if not statements and current:
        statements = ["\n".join(current).strip()]

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
        processed_date=str(len(statements)),
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
