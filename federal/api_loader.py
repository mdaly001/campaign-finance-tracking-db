"""Load FEC data from OpenFEC API (for use when bulk downloads are unavailable)."""

import os
import time
import requests
from sqlalchemy import create_engine, text

API_KEY = os.getenv("FEC_API_KEY", "DEMO_KEY")
BASE_URL = "https://api.open.fec.gov/v1"

def fetch_all(url, params=None):
    """Fetch all pages from a paginated API endpoint."""
    params = params or {}
    params["api_key"] = API_KEY
    params["per_page"] = 100
    
    all_results = []
    page = 1
    
    while True:
        params["page"] = page
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        
        if "results" not in data:
            print(f"API error: {data.get('message', 'unknown')}")
            break
        
        all_results.extend(data["results"])
        print(f"  Page {page}: {len(data['results'])} records (total: {len(all_results)})")
        
        if page >= data["pagination"]["pages"]:
            break
        page += 1
        time.sleep(0.1)  # Rate limiting
    
    return all_results


def load_independent_expenditures(engine):
    """Load Schedule E data (independent expenditures)."""
    print("Loading independent expenditures from FEC API...")
    
    data = fetch_all(
        f"{BASE_URL}/schedules/schedule_e/",
        {"cycle": "2024"}
    )
    
    print(f"  Fetched {len(data)} records")
    
    # Insert into database
    with engine.begin() as conn:
        for row in data:
            try:
                conn.execute(
                    text("""
                    INSERT INTO fec.fec_independent_expenditures (
                        committee_id, disbursement_date, expenditure_amount,
                        payee_name, payee_city, payee_state, payee_zip,
                        disbursement_description, election_date, candidate_id,
                        candidate_name, two_year_transaction_period
                    ) VALUES (:committee_id, :disbursement_date, :expenditure_amount,
                        :payee_name, :payee_city, :payee_state, :payee_zip,
                        :disbursement_description, :election_date, :candidate_id,
                        :candidate_name, 2024)
                    ON CONFLICT (link_id) DO NOTHING
                    """),
                    {
                        "committee_id": row.get("committee_id"),
                        "disbursement_date": row.get("disbursement_dt"),
                        "expenditure_amount": row.get("expenditure_amount"),
                        "payee_name": row.get("payee_name"),
                        "payee_city": row.get("payee_city"),
                        "payee_state": row.get("payee_state"),
                        "payee_zip": row.get("payee_zip"),
                        "disbursement_description": row.get("expenditure_description"),
                        "election_date": row.get("election_dt"),
                        "candidate_id": row.get("candidate", {}).get("candidate_id"),
                        "candidate_name": row.get("candidate", {}).get("name"),
                        "link_id": row.get("link_id"),
                    }
                )
            except Exception as e:
                print(f"  Error inserting row: {e}")
    
    print("  Done loading independent expenditures")


if __name__ == "__main__":
    engine = create_engine("postgresql://cfdb:cfdb@172.19.0.3:5432/cfdb")
    load_independent_expenditures(engine)
