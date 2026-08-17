"""CLI entry point for state (CAL-ACCESS) ingestion.

Usage:
    python -m state.cli fetch      Download latest CAL-ACCESS raw data
    python -m state.cli verify     Verify checksums against checkpoint
    python -m state.cli discover   List all discovered CAL-ACCESS tables
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.etl.logging import setup_logging


def cmd_fetch(args: argparse.Namespace) -> None:
    """Download the latest CAL-ACCESS raw data."""
    setup_logging(level=args.log_level)
    from state.adapter import StateSourceAdapter

    adapter = StateSourceAdapter(cache_dir=Path(args.cache_dir))
    files = adapter.get_source_files()

    print(f"Found {len(files)} TSV files:")
    for f in files:
        print(f"  {f.name}")

    print(f"\nDownload cached at: {args.cache_dir}/dbwebexport.zip")


def cmd_verify(args: argparse.Namespace) -> None:
    """Verify checksums against checkpoint."""
    setup_logging(level=args.log_level)
    from state.adapter import StateSourceAdapter

    adapter = StateSourceAdapter(cache_dir=Path(args.cache_dir))

    if adapter.is_up_to_date():
        print("Cache is up to date")
    else:
        print("Update available")


def cmd_discover(args: argparse.Namespace) -> None:
    """List all discovered CAL-ACCESS tables."""
    setup_logging(level=args.log_level)
    from state.adapter import StateSourceAdapter

    adapter = StateSourceAdapter(cache_dir=Path(args.cache_dir))
    files = adapter.get_source_files()

    print(f"Discovered {len(files)} tables:")
    for f in files:
        table_code = Path(f.name).stem
        print(f"  {table_code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CAL-ACCESS data ingestion")
    subparsers = parser.add_subparsers(dest="command")

    # fetch
    fetch_parser = subparsers.add_parser("fetch", help="Download latest raw data")
    fetch_parser.add_argument("--cache-dir", default="/app/state/cache")
    fetch_parser.add_argument("--log-level", default="INFO")
    fetch_parser.set_defaults(func=cmd_fetch)

    # verify
    verify_parser = subparsers.add_parser("verify", help="Verify checksums")
    verify_parser.add_argument("--cache-dir", default="/app/state/cache")
    verify_parser.add_argument("--log-level", default="INFO")
    verify_parser.set_defaults(func=cmd_verify)

    # discover
    discover_parser = subparsers.add_parser("discover", help="List discovered tables")
    discover_parser.add_argument("--cache-dir", default="/app/state/cache")
    discover_parser.add_argument("--log-level", default="INFO")
    discover_parser.set_defaults(func=cmd_discover)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
