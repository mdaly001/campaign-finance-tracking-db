"""Application settings from environment variables."""

import os
from pathlib import Path


def get_env(name: str, default: str = "") -> str:
    """Get an environment variable, falling back to default."""
    return os.environ.get(name, default)


def get_database_url() -> str:
    """Construct DATABASE_URL from env vars.

    Precedence:
      1. ``DATABASE_URL`` (full override)
      2. ``DB_PASSWORD`` (+ optional DB_USER/DB_HOST/DB_PORT/DB_NAME)

    There is no default password: if neither ``DATABASE_URL`` nor
    ``DB_PASSWORD`` is set this raises rather than silently falling back
    to the stock ``postgres``/``postgres`` pair.
    """
    db_url = get_env("DATABASE_URL")
    if db_url:
        return db_url

    # Build from components. The password is required — no default.
    password = get_env("DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "Neither DATABASE_URL nor DB_PASSWORD is set; cannot build a "
            "database connection. Provide one — there is no default."
        )
    user = get_env("DB_USER", "cfdb")
    host = get_env("DB_HOST", "localhost")
    port = get_env("DB_PORT", "5432")
    dbname = get_env("DB_NAME", "cfdb")

    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def get_log_level() -> str:
    """Get log level from environment."""
    return get_env("LOG_LEVEL", "INFO").upper()


def get_unredacted_enabled() -> bool:
    """Check if unredacted donor data is enabled."""
    return get_env("UNREDACTED_ENABLED", "false").lower() == "true"


def get_mcp_port() -> int:
    """Get MCP server port."""
    return int(get_env("MCP_PORT", "9527"))


def get_donor_watch_since() -> str:
    """Get the date to start donor watch tracking."""
    return get_env("DONOR_WATCH_SINCE", "")


# Project root (parent of config/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
