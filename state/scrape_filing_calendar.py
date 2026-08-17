"""CLI tool: scrape SOS filing calendar and populate the database.

Usage:
    python -m state.scrape_filing_calendar              Scrape + seed + upsert
    python -m state.scrape_filing_calendar --dry-run    Show what would be inserted
    python -m state.scrape_filing_calendar --replace    Replace existing data
    python -m state.scrape_filing_calendar --seed-only  Only seeded data, no scraping

Data sources:
    - Seeded data: Known filing deadlines from the Political Reform Act
    - Scraped data: SOS Campaign & Lobbying pages for current elections

This tool combines seeded entries (manually verified SOS publication data)
with scraped entries (from current SOS pages) for comprehensive coverage.
"""

from __future__ import annotations

import argparse
import logging
import sys

from core.etl.logging import setup_logging
from state.scrapers import (
    format_filing_calendar,
    scrape_filing_calendar,
    seeded_filing_calendar_entries,
    upsert_filing_calendar,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Campaign Finance DB — scrape and populate filing_calendar table"
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres connection URL (default: from DATABASE_URL env or .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be inserted without writing to the database",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete all existing filing_calendar entries before inserting",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only use seeded data, skip SOS page scraping",
    )
    parser.add_argument(
        "--scrape-url",
        default=None,
        help="Override the SOS URL to scrape (default: standard SOS deadlines page)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()
    setup_logging(level=args.log_level)

    # Get database URL
    from config.settings import get_database_url
    database_url = args.database_url or get_database_url()

    # Collect entries
    entries: list = []

    # Always include seeded entries (manually verified SOS publication data)
    if args.seed_only:
        logger.info("Seed-only mode: only using pre-seeded data")
        seeded = seeded_filing_calendar_entries()
        entries.extend(seeded)
        logger.info("Loaded %d seeded entries", len(seeded))
    else:
        # Include seeded entries first
        seeded = seeded_filing_calendar_entries()
        entries.extend(seeded)
        logger.info("Loaded %d seeded entries", len(seeded))

        # Scrape current SOS pages
        scrape_url = args.scrape_url or None
        scraped = scrape_filing_calendar(url=scrape_url)
        entries.extend(scraped)
        logger.info("Scraped %d additional entries", len(scraped))

    # Deduplicate by (election_date, filing_type)
    seen: set[tuple] = set()
    unique: list = []
    for e in entries:
        key = (e.election_date, e.filing_type)
        if key not in seen:
            seen.add(key)
            unique.append(e)

    entries = unique
    logger.info("Total unique entries: %d", len(entries))

    # Show preview
    print(format_filing_calendar(entries))

    if args.dry_run:
        print(f"\n[Dry run] Would upsert {len(entries)} entries")
        return 0

    # Upsert into database
    if args.replace:
        print("\n[Replace] Clearing existing filing_calendar table...")
    inserted, updated = upsert_filing_calendar(
        entries,
        database_url=database_url,
        replace=args.replace,
    )
    print(f"\nInserted: {inserted}, Updated: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
