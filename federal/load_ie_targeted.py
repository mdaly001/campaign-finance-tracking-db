"""Load candidate-targeted independent expenditures (Schedule E) into fec.fec_ie_targeted.

Source: FEC bulk `independent_expenditure_{cycle}.csv` (comma-delimited, with header,
quoted fields). This is the candidate-targeted IE file — distinct from oppexp.txt.

Usage:
    python -m federal.load_ie_targeted --cycle 2026 \
        --csv fec_cache/independent_expenditure_2026.csv \
        --database-url postgresql://cfdb:cfdb@172.19.0.3:5432/cfdb
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime

from sqlalchemy import create_engine, text

# CSV column -> table column
COLMAP = {
    "spe_id": "spender_id",
    "spe_nam": "spender_name",
    "cand_id": "candidate_id",
    "cand_name": "candidate_name",
    "ele_type": "election_type",
    "can_office_state": "office_state",
    "can_office_dis": "office_district",
    "can_office": "office",
    "cand_pty_aff": "party_affiliation",
    "exp_amo": "expenditure_amount",
    "exp_date": "expenditure_date",
    "agg_amo": "aggregate_amount",
    "sup_opp": "support_oppose",
    "pur": "purpose",
    "pay": "payee",
    "file_num": "file_number",
    "amndt_ind": "amendment_indicator",
    "tran_id": "transaction_id",
    "image_num": "image_number",
    "receipt_dat": "receipt_date",
    "fec_election_yr": "election_year",
    "prev_file_num": "previous_file_number",
    "dissem_dt": "dissemination_date",
}
DATE_COLS = {"expenditure_date", "receipt_date", "dissemination_date"}
NUM_COLS = {"expenditure_amount", "aggregate_amount"}
INT_COLS = {"election_year"}


def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d-%b-%y").date()
    except ValueError:
        return None


def _clean(field, col):
    s = (field or "").strip()
    if col in DATE_COLS:
        return _parse_date(s)
    if s == "":
        return None
    if col in NUM_COLS:
        try:
            return float(s)
        except ValueError:
            return None
    if col in INT_COLS:
        try:
            return int(s)
        except ValueError:
            return None
    return s


def load(engine, csv_path, src_file, batch_size=1000):
    table_cols = list(COLMAP.values())
    placeholders = ", ".join(f":{c}" for c in table_cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in table_cols if c != "spender_id")
    stmt = text(
        f"INSERT INTO fec.fec_ie_targeted ({', '.join(table_cols)}, src_file) "
        f"VALUES ({placeholders}, :src_file) "
        f"ON CONFLICT (spender_id, file_number, transaction_id) DO UPDATE SET {updates}, src_file = EXCLUDED.src_file"
    )

    read = ins = 0
    batch = []
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rec = {COLMAP[k]: _clean(v, COLMAP[k]) for k, v in row.items() if k in COLMAP}
            rec["src_file"] = src_file
            batch.append(rec)
            read += 1
            if len(batch) >= batch_size:
                with engine.begin() as conn:
                    conn.execute(stmt, batch)
                ins += len(batch)
                batch = []
                print(f"  loaded {ins}/{read} ...", flush=True)
    if batch:
        with engine.begin() as conn:
            conn.execute(stmt, batch)
        ins += len(batch)
    return read, ins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", required=True, type=int)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--database-url", required=True)
    args = ap.parse_args()

    engine = create_engine(args.database_url)
    read, ins = load(engine, args.csv, src_file=args.csv.split("/")[-1])
    print(f"IE targeted load complete (cycle {args.cycle}): {read} read, {ins} upserted")


if __name__ == "__main__":
    main()
