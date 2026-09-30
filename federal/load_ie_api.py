"""Load the FULL candidate-targeted Schedule E from the OpenFEC API into fec.fec_ie_targeted.

The bulk CSV was only ~1.2% of the real Schedule E. This pulls the authoritative
full cycle from the API (most_recent versions only), maps the richer fields, and
upserts idempotently on the API's unique record id (sub_id).

IMPORTANT API quirks discovered:
  * The cycle filter param is `cycle` (NOT two_year_transaction_period, which is
    silently ignored and returns all years).
  * This endpoint uses CURSOR pagination: the `page` param is ignored. Advance by
    passing `last_index` + `last_expenditure_date` from the previous response's
    `pagination.last_indexes`.
  * `link_id` is NOT unique (links to the candidate object); `sub_id` is the
    unique record id.

Resumable: the cursor + loaded count are checkpointed after every page. Paced to
stay under the 60 req/min key limit, with backoff on HTTP 429.

Usage:
    python -m federal.load_ie_api --cycle 2026 \
        --database-url postgresql://cfdb:cfdb@172.19.0.2:5432/cfdb
"""
from __future__ import annotations

import argparse
import json
import os
import time

import requests
from sqlalchemy import create_engine, text

BASE_URL = "https://api.open.fec.gov/v1"
PAGE_SIZE = 100
REQ_PAUSE = 1.1  # ~54 req/min, safely under the 60/min key limit
CKPT_DIR = "fec_cache"

# API field -> table column
COLMAP = {
    "sub_id": "sub_id",
    "link_id": "link_id",
    "committee_id": "spender_id",
    "candidate_id": "candidate_id",
    "candidate_name": "candidate_name",
    "election_type": "election_type",
    "candidate_office_state": "office_state",
    "candidate_office_district": "office_district",
    "candidate_office": "office",
    "candidate_party": "party_affiliation",
    "expenditure_amount": "expenditure_amount",
    "expenditure_date": "expenditure_date",
    "office_total_ytd": "aggregate_amount",
    "support_oppose_indicator": "support_oppose",
    "expenditure_description": "purpose",
    "payee_name": "payee",
    "file_number": "file_number",
    "amendment_indicator": "amendment_indicator",
    "transaction_id": "transaction_id",
    "image_number": "image_number",
    "filing_date": "receipt_date",
    "report_year": "election_year",
    "previous_file_number": "previous_file_number",
    "dissemination_date": "dissemination_date",
    "filing_form": "filing_form",
    "is_notice": "is_notice",
    "most_recent": "most_recent",
}
TABLE_COLS = list(COLMAP.values())


def _get_api_key() -> str:
    key = os.getenv("FEC_API_KEY")
    if not key:
        raise RuntimeError("FEC_API_KEY not set in environment")
    return key


def _api_get(session, key, params):
    """GET with 429 backoff. Returns parsed JSON."""
    p = dict(params)
    p["api_key"] = key
    for attempt in range(8):
        resp = session.get(f"{BASE_URL}/schedules/schedule_e/", params=p, timeout=90)
        if resp.status_code == 429:
            wait = min(60, 10 * (attempt + 1))
            print(f"  429 rate-limited, backing off {wait}s (attempt {attempt+1})", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("rate limit exhausted after retries")


def _ckpt_path(cycle):
    return os.path.join(CKPT_DIR, f"ie_api_{cycle}_checkpoint.json")


def _load_ckpt(cycle):
    path = _ckpt_path(cycle)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"cycle": cycle, "cursor": None, "loaded": 0, "pages": 0}


def _save_ckpt(cycle, data):
    path = _ckpt_path(cycle)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _map_row(r):
    rec = {}
    for api_f, col in COLMAP.items():
        v = r.get(api_f)
        if isinstance(v, str) and v.strip() == "":
            v = None
        rec[col] = v
    rec["src_file"] = f"fec_api_schedule_e_{r.get('report_year', '')}"
    return rec


def load(engine, cycle, restart=False):
    key = _get_api_key()
    session = requests.Session()
    ckpt = {"cycle": cycle, "cursor": None, "loaded": 0, "pages": 0} if restart else _load_ckpt(cycle)
    cursor = ckpt.get("cursor")
    loaded = ckpt.get("loaded", 0)
    pages = ckpt.get("pages", 0)

    placeholders = ", ".join(f":{c}" for c in TABLE_COLS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in TABLE_COLS if c != "sub_id")
    stmt = text(
        f"INSERT INTO fec.fec_ie_targeted ({', '.join(TABLE_COLS)}, src_file) "
        f"VALUES ({placeholders}, :src_file) "
        f"ON CONFLICT (sub_id) DO UPDATE SET {updates}, src_file = EXCLUDED.src_file"
    )

    base = {"cycle": str(cycle), "most_recent": "true", "per_page": PAGE_SIZE}
    total_count = None
    print(f"Starting API Schedule E load for {cycle} "
          f"(resumed loaded={loaded}, pages={pages}, cursor={'yes' if cursor else 'start'})",
          flush=True)

    while True:
        params = dict(base)
        if cursor:
            params["last_index"] = cursor["last_index"]
            params["last_expenditure_date"] = cursor["last_expenditure_date"]
        d = _api_get(session, key, params)
        results = d.get("results", [])
        total_count = d.get("pagination", {}).get("count")
        if not results:
            break

        batch = [_map_row(r) for r in results]
        with engine.begin() as conn:
            conn.execute(stmt, batch)
        loaded += len(batch)
        pages += 1

        li = d.get("pagination", {}).get("last_indexes")
        if li:
            cursor = {"last_index": li["last_index"],
                      "last_expenditure_date": li["last_expenditure_date"]}
        _save_ckpt(cycle, {"cycle": cycle, "cursor": cursor, "loaded": loaded, "pages": pages})

        if pages % 50 == 0:
            pct = 100.0 * loaded / total_count if total_count else 0
            print(f"  page {pages} loaded={loaded}/{total_count} ({pct:.1f}%)", flush=True)

        time.sleep(REQ_PAUSE)

    print(f"API Schedule E load complete for {cycle}: {loaded} rows upserted "
          f"(API reported {total_count})", flush=True)
    return loaded, total_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", required=True, type=int)
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--restart", action="store_true", help="ignore checkpoint, start from beginning")
    args = ap.parse_args()

    engine = create_engine(args.database_url)
    load(engine, args.cycle, restart=args.restart)


if __name__ == "__main__":
    main()
