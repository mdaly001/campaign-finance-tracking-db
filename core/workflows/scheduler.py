"""Daily scheduler: incremental load + watchdog + entity-resolution hook.

Usage:
    python -m core.workflows.scheduler run        Run one scheduled cycle
    python -m core.workflows.scheduler --help     Show help

This module is the entry point for the ETL Docker service:
    docker compose run --rm etl python -m core.workflows.scheduler run

Each scheduled cycle:
1. Run incremental load (IncrementalLoadRunner)
2. Run donor-watch hook (placeholder — future: check for unusual donor activity)
3. Log summary and exit with code 0 (success) or 1 (failure)

For cron-based scheduling (outside Docker), run the same command
from a crontab entry, e.g.:
    0 2 * * * cd /app && python -m core.workflows.scheduler run >> /var/log/cfdb/scheduler.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from core.etl.logging import setup_logging

logger = logging.getLogger(__name__)


def run_scheduler(
    database_url: str | None = None,
    cache_dir: Path | None = None,
    batch_size: int = 1000,
    log_level: str = "INFO",
) -> int:
    """Execute one scheduled cycle.

    Returns:
        0 on success, 1 on failure.
    """
    start = time.monotonic()
    setup_logging(level=log_level)

    database_url = database_url or "postgresql://cfdb:cfdb@localhost:5432/cfdb"
    cache_dir = cache_dir or Path("/app/state/cache")

    print("\n" + "=" * 70)
    print("SCHEDULER CYCLE — " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)

    # Step 1: Incremental load
    print("\n[Step 1/3] Running incremental load...")
    from state.etl import IncrementalLoadRunner

    runner = IncrementalLoadRunner(
        database_url=database_url,
        cache_dir=cache_dir,
        batch_size=batch_size,
    )
    result = runner.run()

    if result.tables_loaded > 0:
        print(
            f"  ✓ Loaded {result.tables_loaded} table(s), "
            f"{result.total_rows_upserted:,} rows upserted, "
            f"{result.total_duration_seconds:.1f}s"
        )
    else:
        print(
            f"  ✓ No updates needed "
            f"({result.tables_checked} tables checked, "
            f"{result.tables_skipped} skipped)"
        )

    # Step 2: Donor-watch hook (placeholder)
    print("\n[Step 2/3] Running donor-watch hook...")
    print("  ℹ Donor-watch hook is a placeholder — future: check for unusual donor activity")

    # Step 3: Summary
    elapsed = time.monotonic() - start
    print(f"\n[Step 3/3] Cycle complete in {elapsed:.1f}s")

    # Log structured summary
    logger.info(
        "scheduler.cycle tables_loaded=%d tables_skipped=%d rows_upserted=%d duration=%.2fs",
        result.tables_loaded,
        result.tables_skipped,
        result.total_rows_upserted,
        round(elapsed, 2),
    )

    print("\n" + "=" * 70)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Campaign Finance DB — daily scheduler (incremental load + hooks)"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run"],
        help="Command to run (default: run)",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--cache-dir", default="/app/state/cache")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()

    if args.command == "run":
        exit_code = run_scheduler(
            database_url=args.database_url,
            cache_dir=Path(args.cache_dir),
            batch_size=args.batch_size,
            log_level=args.log_level,
        )
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
