"""Central configuration resolution.

Keeps credential handling in one place so there are no hardcoded
connection-string defaults scattered across the ETL and tooling. A
hardcoded default (a literal ``user:password@host`` in a connection
string) puts a known credential in the repository and silently points a
script at the wrong database; every entry point should resolve its URL
explicitly and fail loudly when it is missing.
"""

from __future__ import annotations

import os


def require_database_url(component: str = "this component") -> str:
    """Return ``DATABASE_URL`` or raise a clear error if it is unset.

    There is deliberately no fallback connection string. Callers use this
    instead of an ``or "postgresql://..."`` default so a missing
    configuration is an immediate, actionable error rather than a silent
    connection to a guessed host with a guessed password.

    Args:
        component: Human-readable name included in the error so the
            operator knows which entry point needs configuring.

    Returns:
        The ``DATABASE_URL`` value.

    Raises:
        RuntimeError: if ``DATABASE_URL`` is unset or empty.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    raise RuntimeError(
        f"DATABASE_URL is not set; {component} cannot connect to the "
        "database. Set DATABASE_URL (see .env.example) — there is no "
        "built-in default."
    )
