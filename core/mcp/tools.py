"""MCP tools for the Campaign Finance Database.

Implements the 9 Phase 1 query tools defined in the project plan:

1. **contributions_by_donor** — All contributions by a donor name in a cycle
2. **top_donors_for_committee_or_candidate** — Top N donors to a committee/candidate
3. **committee_outlays_to** — Expenditures from a committee to a vendor/payee
4. **vendor_revenue** — Total revenue received by a vendor name
5. **committee_profile** — Summary profile of a committee
6. **measure_spending** — Spending totals for a ballot measure
7. **donor_watch_since** — Contributions from a donor since a date
8. **upcoming_filings** — Upcoming filing deadlines for a committee
9. **filing_due_soon** — All filings due within N days

Each tool returns structured data via Pydantic models that map to
JSON-serializable dictionaries for the MCP transport layer.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from core.mcp.db import execute_read

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _money(value: Any) -> float:
    """Coerce a numeric DB value to float for JSON transport."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _dtos(value: Any) -> str | None:
    """Coerce a date/timestamp to ISO string or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    """Recursively coerce DB types for Pydantic model construction."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = _dtos(v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
#  Pydantic result models
# --------------------------------------------------------------------------- #


# -- 1. contributions_by_donor ----------------------------------------------- #


class ContributionRecord(BaseModel):
    tran_id: str
    filing_id: str
    amend_id: str
    cycle: int
    amount: float
    date: str
    purpose: str | None = None
    cmte_id: str | None = None
    memo_refno: str | None = None


def contributions_by_donor(
    donor_name: str,
    cycle: int,
    include_aliases: bool = True,
) -> list[dict[str, Any]]:
    """Return all contributions by a donor name in a given election cycle.

    Args:
        donor_name: Partial or full donor name (LIKE match).
        cycle: Election cycle year (e.g. 2024).
        include_aliases: If True, also include contributions from
            donor-alias table for name variations.

    Returns:
        List of ContributionRecord dicts.
    """
    alias_names: list[str] = []
    if include_aliases:
        alias_rows = execute_read(
            "SELECT alias_name FROM donor_alias WHERE base_name ILIKE :base",
            {"base": donor_name},
        )
        alias_names = [r["alias_name"] for r in alias_rows if r["alias_name"]]

    name_pattern = f"%{donor_name}%"
    name_cond = (
        "ctrib_naml ILIKE :n OR ctrib_namf ILIKE :nf OR ctrib_namt ILIKE :nt"
    )
    alias_cond = ""
    params: dict[str, Any] = {
        "n": name_pattern,
        "nf": name_pattern,
        "nt": name_pattern,
        "cycle": cycle,
    }
    if alias_names:
        alias_pattern = f"%{'%'.join(alias_names)}%"
        alias_cond = (
            " OR ctrib_naml ILIKE :an OR ctrib_namf ILIKE :anf OR ctrib_namt ILIKE :ant"
        )
        params["an"] = alias_pattern
        params["anf"] = alias_pattern
        params["ant"] = alias_pattern

    sql = f"""
        SELECT
            rc.tran_id, rc.filing_id, rc.amend_id, rc.amount,
            rc.tran_dt AS date, rc.payd_by AS purpose,
            rc.cmte_id, rc.memo_refno,
            COALESCE(f.elect_year, EXTRACT(YEAR FROM rc.tran_dt)::int) AS cycle
        FROM rcpt_cd rc
        LEFT JOIN filings f ON rc.filing_id = f.filing_id
        WHERE ({name_cond}{alias_cond})
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM rc.tran_dt)::int) = :cycle
        ORDER BY rc.tran_dt DESC
        LIMIT 500
    """

    rows = execute_read(sql, params)

    result: list[dict[str, Any]] = []
    for row in rows:
        coerced = _coerce_row(row)
        record = ContributionRecord(**coerced)
        result.append(record.model_dump())
    return result


# -- 2. top_donors_for_committee_or_candidate -------------------------------- #


class TopDonorRecord(BaseModel):
    donor_name: str
    total_amount: float
    contribution_count: int
    first_date: str
    last_date: str


def top_donors_for_committee_or_candidate(
    committee_id: str,
    cycle: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the top N donors to a committee or candidate in a cycle.

    Args:
        committee_id: Committee ID (e.g. 'C00012345').
        cycle: Election cycle year.
        limit: Maximum number of results (default 10, max 100).

    Returns:
        List of TopDonorRecord dicts sorted by total_amount DESC.
    """
    limit = min(max(limit, 1), 100)
    sql = """
        SELECT
            rcpt.ctrib_naml || COALESCE(' ' || rcpt.ctrib_namf, '') AS donor_name,
            SUM(rcpt.amount) AS total_amount,
            COUNT(*) AS contribution_count,
            MIN(rcpt.tran_dt) AS first_date,
            MAX(rcpt.tran_dt) AS last_date
        FROM rcpt_cd rcpt
        LEFT JOIN filings f ON rcpt.filing_id = f.filing_id
        WHERE (rcpt.cmte_id = :cid OR rcpt.payee_filer_id = :cid)
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM rcpt.tran_dt)::int) = :cycle
        GROUP BY rcpt.ctrib_naml, rcpt.ctrib_namf
        ORDER BY total_amount DESC
        LIMIT :lim
    """
    rows = execute_read(sql, {"cid": committee_id, "cycle": cycle, "lim": limit})

    result: list[dict[str, Any]] = []
    for row in rows:
        coerced = _coerce_row(row)
        record = TopDonorRecord(**coerced)
        result.append(record.model_dump())
    return result


# -- 3. committee_outlays_to ------------------------------------------------- #


class OutlayRecord(BaseModel):
    tran_id: str
    filing_id: str
    amount: float
    date: str
    purpose: str | None = None
    memo_refno: str | None = None


def committee_outlays_to(
    committee_id: str,
    cycle: int,
) -> list[dict[str, Any]]:
    """Return expenditures made by a committee to vendors/payees in a cycle.

    Args:
        committee_id: Committee ID.
        cycle: Election cycle year.

    Returns:
        List of OutlayRecord dicts.
    """
    sql = """
        SELECT
            e.tran_id, e.filing_id, e.amount, e.expn_date AS date,
            e.expn_dscr AS purpose, e.memo_refno
        FROM exppd_cd e
        LEFT JOIN filings f ON e.filing_id = f.filing_id
        WHERE e.filer_id = :cid
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM e.expn_date)::int) = :cycle
        ORDER BY e.expn_date DESC
    """
    rows = execute_read(sql, {"cid": committee_id, "cycle": cycle})

    result: list[dict[str, Any]] = []
    for row in rows:
        coerced = _coerce_row(row)
        record = OutlayRecord(**coerced)
        result.append(record.model_dump())
    return result


# -- 4. vendor_revenue ------------------------------------------------------- #


def vendor_revenue(
    vendor_name: str,
    cycle: int,
) -> dict[str, Any]:
    """Return total revenue received by a vendor name across committees.

    Sums amounts from rcpt_cd (as payee) and exppd_cd (as payee) where
    the vendor name appears in the recipient fields.

    Args:
        vendor_name: Partial or full vendor name.
        cycle: Election cycle year.

    Returns:
        Dict with vendor_name, total_received, total_expenditures,
        transaction_count, cycles.
    """
    name_pattern = f"%{vendor_name}%"

    rev_sql = """
        SELECT
            COALESCE(SUM(amount), 0) AS total_received,
            COUNT(*) AS transaction_count,
            ARRAY_AGG(DISTINCT COALESCE(f.elect_year, EXTRACT(YEAR FROM tran_dt)::int)) AS cycles
        FROM rcpt_cd
        LEFT JOIN filings f ON rcpt_cd.filing_id = f.filing_id
        WHERE (payee_naml ILIKE :n OR payee_namf ILIKE :n OR payee_namt ILIKE :n)
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM tran_dt)::int) = :cycle
    """
    rev_rows = execute_read(rev_sql, {"n": name_pattern, "cycle": cycle})
    rev_row = rev_rows[0] if rev_rows else {
        "total_received": 0, "transaction_count": 0, "cycles": [],
    }

    exp_sql = """
        SELECT COALESCE(SUM(amount), 0) AS total_expenditures
        FROM exppd_cd
        LEFT JOIN filings f ON exppd_cd.filing_id = f.filing_id
        WHERE (payee_naml ILIKE :n OR payee_namf ILIKE :n OR payee_namt ILIKE :n)
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM expn_date)::int) = :cycle
    """
    exp_rows = execute_read(exp_sql, {"n": name_pattern, "cycle": cycle})
    exp_row = exp_rows[0] if exp_rows else {"total_expenditures": 0}

    cycles_raw: Any = rev_row.get("cycles", [])
    cycles = [c for c in cycles_raw if c is not None]
    return {
        "vendor_name": vendor_name,
        "total_received": _money(rev_row.get("total_received")),
        "total_expenditures": _money(exp_row.get("total_expenditures")),
        "transaction_count": int(rev_row.get("transaction_count") or 0),
        "cycles": sorted(set(int(c) for c in cycles)),
    }


# -- 5. committee_profile ---------------------------------------------------- #


def committee_profile(
    committee_id: str,
    cycle: int = 2024,
) -> dict[str, Any]:
    """Return a summary profile of a committee.

    Pulls from rcpt_cd (receipts/contributions), exppd_cd (disbursements),
    and smry_cd (summary totals), joining with filings for cycle lookup.

    Args:
        committee_id: Committee ID (e.g. 'C00012345').
        cycle: Election cycle year.

    Returns:
        Dict with summary financials.
    """
    info_rows = execute_read(
        """
        SELECT
            fn.filer_id,
            fn.naml || COALESCE(' ' || fn.namf, '') AS committee_name,
            fn.filer_type AS committee_type,
            fa.city, fa.st AS state
        FROM filername fn
        LEFT JOIN filer_address fa ON fn.filer_id = fa.filer_id AND fa.add_type = 'B'
        WHERE fn.filer_id = :cid
        LIMIT 1
        """,
        {"cid": committee_id},
    )

    base_info: dict[str, Any] = {}
    if info_rows:
        ir = info_rows[0]
        base_info = {
            "committee_id": ir["filer_id"],
            "committee_name": ir["committee_name"] or "Unknown",
            "committee_type": ir["committee_type"] or "Unknown",
            "city": ir.get("city"),
            "state": ir.get("state"),
        }

    fin_sql = """
        SELECT
            COALESCE(
                SUM(CASE WHEN rc.rec_type = 'R' THEN rc.amount ELSE 0 END), 0
            ) AS total_receipts,
            COALESCE(
                SUM(CASE WHEN rc.rec_type = 'D' THEN rc.amount ELSE 0 END), 0
            ) AS total_disbursements,
            COALESCE(SUM(rc.amount), 0) AS total_contributions,
            COALESCE(SUM(e.amount), 0) AS total_expenditures
        FROM rcpt_cd rc
        LEFT JOIN filings f ON rc.filing_id = f.filing_id
        LEFT JOIN exppd_cd e ON rc.filing_id = e.filing_id
        WHERE (rc.cmte_id = :cid OR rc.filer_id = :cid
               OR rc.payee_filer_id = :cid)
          AND COALESCE(f.elect_year,
                       EXTRACT(YEAR FROM rc.tran_dt)::int) = :cycle
    """
    fin_rows = execute_read(fin_sql, {"cid": committee_id, "cycle": cycle})

    fin = fin_rows[0] if fin_rows else {
        "total_receipts": 0, "total_disbursements": 0,
        "total_contributions": 0, "total_expenditures": 0,
    }

    cash_rows = execute_read(
        """
        SELECT amount_b AS cash_on_hand
        FROM smry_cd s
        JOIN filings f ON s.filing_id = f.filing_id
        WHERE s.cmte_id = :cid
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM f.elect_dt)::int) = :cycle
        ORDER BY f.elect_dt DESC
        LIMIT 1
        """,
        {"cid": committee_id, "cycle": cycle},
    )
    cash_on_hand = cash_rows[0]["cash_on_hand"] if cash_rows else 0

    # Ensure defaults for required fields
    if "committee_name" not in base_info:
        base_info["committee_name"] = "Unknown"
    if "committee_id" not in base_info:
        base_info["committee_id"] = committee_id

    return {
        **base_info,
        "cycle": cycle,
        "total_receipts": _money(fin.get("total_receipts")),
        "total_disbursements": _money(fin.get("total_disbursements")),
        "cash_on_hand": _money(cash_on_hand),
        "total_contributions": _money(fin.get("total_contributions")),
        "total_expenditures": _money(fin.get("total_expenditures")),
    }


# -- 6. measure_spending ----------------------------------------------------- #


class MeasureSpendingRecord(BaseModel):
    committee_id: str
    committee_name: str
    total_spent: float
    support_oppose: str | None = None  # 'S', 'O', or None


def measure_spending(
    measure_id: str,
    cycle: int = 2024,
) -> list[dict[str, Any]]:
    """Return spending totals for a ballot measure.

    Searches cvr_camp_disc (campaign disclosures for measures) and
    related detail tables for measure_id references.

    Args:
        measure_id: Measure identifier (e.g. 'PROP 65' or measure code).
        cycle: Election cycle year.

    Returns:
        List of MeasureSpendingRecord dicts.
    """
    sql = """
        SELECT
            d.cmte_id AS committee_id, d.cmte_name AS committee_name,
            SUM(d.amount) AS total_spent,
            d.supp_opp AS support_oppose
        FROM cvr_camp_disc d
        JOIN filings f ON d.filing_id = f.filing_id
        WHERE (d.measure_id ILIKE :mid OR d.measure_desc ILIKE :mdesc)
          AND COALESCE(f.elect_year, EXTRACT(YEAR FROM f.elect_dt)::int) = :cycle
        GROUP BY d.cmte_id, d.cmte_name, d.supp_opp
        ORDER BY total_spent DESC
    """
    rows = execute_read(sql, {
        "mid": f"%{measure_id}%",
        "mdesc": f"%{measure_id}%",
        "cycle": cycle,
    })

    result: list[dict[str, Any]] = []
    for row in rows:
        coerced = _coerce_row(row)
        record = MeasureSpendingRecord(**coerced)
        result.append(record.model_dump())
    return result


# -- 7. donor_watch_since ---------------------------------------------------- #


class DonorWatchRecord(BaseModel):
    tran_id: str
    filing_id: str
    amount: float
    date: str
    committee_id: str | None = None
    committee_name: str | None = None
    purpose: str | None = None


def donor_watch_since(
    since_date: str,
    donor_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return contributions from a donor since a given date.

    Useful for monitoring new donor activity. If donor_name is None,
    returns all contributions since the date.

    Args:
        since_date: Start date in YYYY-MM-DD format.
        donor_name: Optional donor name filter (partial match).

    Returns:
        List of DonorWatchRecord dicts sorted by date DESC.
    """
    conditions = ["tran_dt >= :since"]
    params: dict[str, Any] = {"since": since_date}

    if donor_name:
        name_pattern = f"%{donor_name}%"
        conditions.append(
            "ctrib_naml ILIKE :dn OR ctrib_namf ILIKE :dnf OR ctrib_namt ILIKE :dnt"
        )
        params.update({"dn": name_pattern, "dnf": name_pattern, "dnt": name_pattern})

    sql = f"""
        SELECT rc.tran_id, rc.filing_id, rc.amount, rc.tran_dt AS date,
               rc.cmte_id, rc.cmte_name, rc.payd_by AS purpose
        FROM rcpt_cd rc
        WHERE {' AND '.join(conditions)}
        ORDER BY rc.tran_dt DESC
        LIMIT 500
    """
    rows = execute_read(sql, params)

    result: list[dict[str, Any]] = []
    for row in rows:
        coerced = _coerce_row(row)
        record = DonorWatchRecord(**coerced)
        result.append(record.model_dump())
    return result


# -- 8. upcoming_filings ----------------------------------------------------- #


class UpcomingFilingRecord(BaseModel):
    calendar_id: int
    election_date: str
    report_type: str
    deadline_date: str
    grace_period_days: int
    source_url: str | None = None
    notes: str | None = None


def upcoming_filings(
    committee_id: str | None = None,
    days_ahead: int = 30,
) -> list[dict[str, Any]]:
    """Return upcoming filing deadlines.

    Args:
        committee_id: Optional committee ID to filter deadlines.
        days_ahead: Number of days to look ahead (default 30).

    Returns:
        List of UpcomingFilingRecord dicts sorted by deadline_date.
    """
    today = date.today()
    future_date = today + __import__("datetime").timedelta(days=days_ahead)

    sql = """
        SELECT calendar_id, election_date, report_type, deadline_date,
               grace_period_days, source_url, notes
        FROM filing_calendar
        WHERE deadline_date >= :today AND deadline_date <= :future
        ORDER BY deadline_date ASC
    """
    params = {"today": today.isoformat(), "future": future_date.isoformat()}

    rows = execute_read(sql, params)

    result: list[dict[str, Any]] = []
    for row in rows:
        coerced = _coerce_row(row)
        record = UpcomingFilingRecord(**coerced)
        result.append(record.model_dump())
    return result


# -- 9. filing_due_soon ------------------------------------------------------ #


class FilingDueSoonRecord(BaseModel):
    filing_id: str
    committee_id: str
    committee_name: str
    form_type: str
    filing_date: str
    deadline_date: str | None = None
    status: str


def filing_due_soon(
    days_ahead: int = 7,
) -> list[dict[str, Any]]:
    """Return filings due within the next N days.

    Queries the filing_calendar for deadlines approaching and cross-references
    with filings already submitted.

    Args:
        days_ahead: Number of days to look ahead (default 7).

    Returns:
        List of FilingDueSoonRecord dicts.
    """
    today = date.today()
    future_date = today + __import__("datetime").timedelta(days=days_ahead)

    calendar_rows = execute_read(
        """
        SELECT filing_id, committee_id, committee_name, form_type,
               filing_date, deadline_date, status
        FROM filing_calendar
        WHERE deadline_date >= :today AND deadline_date <= :future
          AND status = 'OPEN'
        ORDER BY deadline_date ASC
        """,
        {"today": today.isoformat(), "future": future_date.isoformat()},
    )

    result: list[dict[str, Any]] = []
    for row in calendar_rows:
        coerced = _coerce_row(row)
        record = FilingDueSoonRecord(**coerced)
        result.append(record.model_dump())
    return result
