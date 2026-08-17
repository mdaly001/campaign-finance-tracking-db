"""CLI tool: scrape SOS election results PDFs and populate the database.

Usage:
    python -m state.scrape_election_results                 Scrape + upsert
    python -m state.scrape_election_results --dry-run       Show what would be inserted
    python -m state.scrape_election_results --replace       Replace existing data
    python -m state.scrape_election_results --download-dir /path  Where to save PDFs

Data sources:
    - SOS Elections pages: https://www.sos.ca.gov/elections/
    - SOS Previous Elections: https://www.sos.ca.gov/elections/previous-elections/
    - SOS Upcoming Elections: https://www.sos.ca.gov/elections/upcoming-elections/

This tool discovers PDF election results reports and downloads them
to a local directory for downstream parsing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from core.etl.logging import setup_logging
from state.scrapers import (
    format_election_results,
    scrape_election_results_pdf_links,
    upsert_election_results,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Campaign Finance DB — scrape and populate election_results table"
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
        help="Delete all existing election_results entries before inserting",
    )
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Directory to save downloaded PDFs (default: ./state/cache/election_results/)",
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

    # Determine download directory
    download_dir = None
    if args.download_dir:
        download_dir = Path(args.download_dir)
    else:
        download_dir = Path("state/cache/election_results")

    # Scrape
    print("Scraping SOS election results pages...")
    entries = scrape_election_results_pdf_links(
        download_dir=download_dir,
    )

    if not entries:
        print("No election results PDFs found.")
        return 0

    # Show preview
    print(format_election_results(entries))
    print(f"\nTotal discovered: {len(entries)}")

    if args.dry_run:
        print(f"\n[Dry run] Would upsert {len(entries)} entries")
        return 0

    # Upsert into database
    if args.replace:
        print("\n[Replace] Clearing existing election_results table...")
    inserted, updated = upsert_election_results(
        entries,
        database_url=database_url,
        replace=args.replace,
    )
    print(f"\nInserted: {inserted}, Updated: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
