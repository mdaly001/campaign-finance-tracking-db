#!/usr/bin/env python3
"""CFDB scheduled data updater (state + federal).

Modes:
  daemon  Resident loop: incremental Mon-Fri 01:00, full reload Sat 02:00 (local TZ).
  daily   One incremental update now: reload ONLY state tables whose snapshot
          file-hash changed since the last load, then refresh federal IE.
  weekly  One full update now: re-ingest the whole state snapshot via upserts,
          then refresh federal IE. This is a reload, NOT a drop/rebuild — it
          self-heals drift without a schema-downtime window.

State side: the CA SOS publishes only full snapshots (dbwebexport.zip).
`state.etl incremental` downloads the current snapshot and, via the
load_checkpoint file hashes, reloads only the tables that actually changed —
i.e. "just what's new since the last pull".

Federal side: re-download the FEC targeted-IE bulk CSV and upsert it into
fec.fec_ie_targeted (keyed on the BULK-synthesized sub_id), then refresh
fec.fec_ie_targeted_resolved.

Env:
  DATABASE_URL   postgres URL (default built from DB_PASSWORD against host 'db')
  DB_PASSWORD    superuser password (used if DATABASE_URL unset)
  FEC_IE_CSV_URL override the 2026 targeted-IE CSV URL
  STATE_CACHE_DIR cache dir for the SOS zip (default /app/state/cache)

Designed to run inside the cfdb-app image (uses its /app/.venv python with
sqlalchemy + the state/federal modules). Stdlib + sqlalchemy only.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta

LOCK_PATH = "/tmp/cfdb_updater.lock"
FEC_IE_CSV_URL = os.getenv(
    "FEC_IE_CSV_URL",
    "https://www.fec.gov/files/bulk-downloads/2026/independent_expenditure_2026.csv",
)
FEC_IE_CYCLE = os.getenv("FEC_IE_CYCLE", "2026")
STATE_CACHE_DIR = os.getenv("STATE_CACHE_DIR", "/app/state/cache")
BATCH_SIZE = os.getenv("CFDB_BATCH_SIZE", "500")


def log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    pw = os.getenv("DB_PASSWORD")
    if not pw:
        raise RuntimeError("DATABASE_URL or DB_PASSWORD must be set")
    return f"postgresql://cfdb:{pw}@db:5432/cfdb"


def _run(desc: str, argv: list[str]) -> None:
    log(f"START {desc}")
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd="/app")
    dt = time.monotonic() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"{desc} failed rc={proc.returncode} after {dt:.0f}s")
    log(f"DONE  {desc} ({dt:.0f}s)")


def federal_refresh() -> None:
    """Download the targeted-IE bulk CSV, upsert, refresh the resolution matview."""
    url = FEC_IE_CSV_URL
    cycle = FEC_IE_CYCLE
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
        csv_path = fh.name
    try:
        log(f"federal: downloading {url}")
        with urllib.request.urlopen(url, timeout=600) as resp, open(csv_path, "wb") as out:
            out.write(resp.read())
        _run(
            "federal load_ie_targeted",
            [sys.executable, "-m", "federal.load_ie_targeted",
             "--cycle", cycle, "--csv", csv_path, "--database-url", database_url()],
        )
        from sqlalchemy import create_engine, text  # in-image dependency
        eng = create_engine(database_url())
        with eng.begin() as conn:
            conn.execute(text("REFRESH MATERIALIZED VIEW fec.fec_ie_targeted_resolved"))
        log("federal: matview fec_ie_targeted_resolved refreshed")
    finally:
        try:
            os.unlink(csv_path)
        except OSError:
            pass


def daily() -> None:
    _run(
        "state incremental (changed tables only)",
        [sys.executable, "-m", "state.etl", "incremental",
         "--database-url", database_url(),
         "--cache-dir", STATE_CACHE_DIR,
         "--batch-size", BATCH_SIZE],
    )
    federal_refresh()


def weekly() -> None:
    _run(
        "state full reload (upsert-based, no drop)",
        [sys.executable, "-m", "state.etl", "full",
         "--database-url", database_url(),
         "--cache-dir", STATE_CACHE_DIR,
         "--batch-size", BATCH_SIZE],
    )
    federal_refresh()


def next_weekday_1am(now: datetime) -> datetime:
    """Next Mon-Fri 01:00 strictly after `now`."""
    for i in range(0, 8):
        day = (now + timedelta(days=i)).replace(hour=1, minute=0, second=0, microsecond=0)
        if day > now and day.weekday() <= 4:
            return day
    raise RuntimeError("unreachable")


def next_saturday_2am(now: datetime) -> datetime:
    """Next Saturday 02:00 strictly after `now`."""
    for i in range(0, 8):
        day = (now + timedelta(days=i)).replace(hour=2, minute=0, second=0, microsecond=0)
        if day > now and day.weekday() == 5:
            return day
    raise RuntimeError("unreachable")


def with_lock(fn):
    """Serialize update runs: a long weekly full must not overlap a daily."""
    def wrapped():
        lock = open(LOCK_PATH, "w")
        log("waiting for update lock...")
        fcntl.flock(lock, fcntl.LOCK_EX)
        log("update lock acquired")
        try:
            fn()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    return wrapped


def daemon() -> None:
    log("daemon up: daily=Mon-Fri 01:00, weekly=Sat 02:00 (TZ=%s)"
        % (os.getenv("TZ", "system")))
    daily_locked = with_lock(daily)
    weekly_locked = with_lock(weekly)
    while True:
        now = datetime.now()
        d = next_weekday_1am(now)
        w = next_saturday_2am(now)
        if d <= w:
            due, fn, label = d, daily_locked, "daily"
        else:
            due, fn, label = w, weekly_locked, "weekly"
        log(f"next {label} run at {due.isoformat(timespec='minutes')}")
        while datetime.now() < due:
            time.sleep(min(60.0, (due - datetime.now()).total_seconds()))
        try:
            fn()
        except Exception as e:  # never let one bad run kill the schedule
            log(f"ERROR {label} run failed: {e}")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if mode == "daily":
        with_lock(daily)()
    elif mode == "weekly":
        with_lock(weekly)()
    elif mode == "daemon":
        daemon()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
