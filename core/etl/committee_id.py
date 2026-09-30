"""Refresh the committee-id derivation materialized views (migration 0020).

WHY THIS EXISTS
---------------
Migration 0020 fixed a real bug: ~94% of CAL-ACCESS receipt/expenditure rows
have a blank cmte_id, so the recipient/spender committee is only recoverable
via the filing that reported it (filing_id -> filer_filings_cd.filer_id). The
derivation lives in three MATERIALIZED VIEWS:

    filing_filer   : one filer per filing (the recipient/spender mapping)
    receipts_all   : contributions with cmte_id derived for blank rows
    expn_all       : expenditures with cmte_id derived for blank rows

Because they are materialized (not plain views) they do NOT update when the
base tables change — they must be refreshed after any load that touches
filer_filings_cd or the fact tables (rcpt_cd / expn_cd / lexp_cd / s496_cd /
s497_cd / s498_cd). Call refresh_committee_views() at the end of a CAL-ACCESS
load run.

REFRESH ... CONCURRENTLY is used so readers (the MCP tools) are never blocked
while the refresh runs; this relies on the unique indexes created in 0020.
filing_filer is refreshed FIRST because receipts_all/expn_all join to it, so
they must see the freshly refreshed mapping.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import Engine, create_engine, text

logger = logging.getLogger(__name__)

# Refresh order matters: the base mapping first, then the views that join it.
_REFRESH_ORDER = ("filing_filer", "receipts_all", "expn_all")


def refresh_committee_views(engine: Engine, *, concurrent: bool = True) -> None:
    """Refresh filing_filer, receipts_all and expn_all after a load.

    Args:
        engine: SQLAlchemy engine pointed at the cfdb database.
        concurrent: Use REFRESH ... CONCURRENTLY (default) so reads are not
            blocked. Requires the unique indexes from migration 0020. Pass
            False only for an initial/first-time build where a lock is fine
            (CONCURRENTLY fails on a never-populated matview).
    """
    verb = "REFRESH MATERIALIZED VIEW CONCURRENTLY" if concurrent else "REFRESH MATERIALIZED VIEW"
    for name in _REFRESH_ORDER:
        logger.info("Refreshing committee-id view: %s (concurrent=%s)", name, concurrent)
        with engine.begin() as conn:
            conn.execute(text(f"{verb} {name}"))
    logger.info("Committee-id views refreshed.")


def _main() -> None:
    """Standalone entry: refresh the committee-id views from DATABASE_URL.

    Useful after a manual data change or if a load's refresh step failed:
        python -m core.etl.committee_id
    Reads DATABASE_URL (falls back to the same env the MCP server uses).
    """
    logging.basicConfig(level=logging.INFO)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    engine = create_engine(url)
    refresh_committee_views(engine)
    engine.dispose()


if __name__ == "__main__":
    _main()
