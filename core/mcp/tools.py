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

Real-schema conventions (CAL-ACCESS export, 2002 data model):

- Detail tables (``rcpt_cd``, ``expn_cd``) have no election-year column; the
  report "cycle" is derived from the transaction date
  (``EXTRACT(YEAR FROM rcpt_date)`` / ``EXTRACT(YEAR FROM expn_date)``).
- Committee IDs on detail tables (``cmte_id``, VARCHAR) map to names via
  ``filer_xref_cd`` (``xref_id`` = cmte_id, ``filer_id`` = filername key)
  joined to ``filername_cd``.
- Contributor names: individuals in ``ctrib_naml``/``ctrib_namf``,
  organizations in ``ctrib_dscr``. Payees: ``payee_naml``/``payee_namf``.
- Aliases come from the scraper-owned ``entity_alias`` table (empty until
  entity resolution has run); matching degrades gracefully to direct names.

Each tool returns structured data via Pydantic models that map to
JSON-serializable dictionaries for the MCP transport layer.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
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
    """Coerce DB types (Decimal/date) for JSON transport."""
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


def _name_parts(name: str) -> dict[str, str]:
    """Build LIKE patterns for a person or organization name.

    Accepts ``"Last, First M"`` or ``"First M Last"`` (and bare names) and
    returns:
      - last:      LIKE pattern for the last-name field
      - first_mid: LIKE pattern for the first-name/middle-initial field
      - full:      the whole string (for description/org fields)
      - base:      the raw string (for alias lookups)
    """
    name = (name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        last, first = last.strip(), first.strip()
    else:
        tokens = name.split()
        if len(tokens) == 1:
            last, first = tokens[0], ""
        elif len(tokens) == 2:
            first, last = tokens[0], tokens[1]
        else:
            first = " ".join(tokens[:-1])
            last = tokens[-1]
    first_mid = (first + " %").strip()  # "John J %" or "%"
    return {
        "last": f"%{last}%",
        "first_mid": first_mid,
        "full": f"%{name}%",
        "base": name,
    }


def _vendor_regex(name: str) -> str:
    """Build a word-boundary-anchored, case-insensitive regex for a
    vendor/payee name.

    Payee names are organizations (not "last name" fields), so the
    ``_name_parts`` last-token substring match over-counts:
    ``"AL Media"`` -> ``%Media%`` would also match ``CENTRAL MEDIA``,
    ``LOCAL MEDIA``, ``GCW MEDIA``... Anchoring on a word boundary
    (``\\m``) makes ``"AL Media"`` match ``AL MEDIA`` / ``AL MEDIA LLC``
    but NOT ``CENTRAL MEDIA``. Used with the SQL ``~*`` operator.
    """
    return r"\m" + re.sub(r"([.\\+*?[](){}^$|])", r"\\\1", (name or "").strip())


def _alias_names(base: str, limit: int = 50) -> list[str]:
    """Look up alias variations in the scraper-owned entity_alias table.

    Returns an empty list when the table has no matching rows (the normal
    state until entity resolution has populated it).
    """
    try:
        rows = execute_read(
            "SELECT alias_name FROM entity_alias "
            "WHERE alias_name ILIKE :base LIMIT :lim",
            {"base": f"%{base}%", "lim": limit},
        )
        return [r["alias_name"] for r in rows if r.get("alias_name")]
    except Exception as e:  # table missing / not populated — degrade gracefully
        logger.debug("entity_alias lookup unavailable: %s", e)
        return []


def _resolve_filer_id(committee_id: str) -> int:
    """Resolve a committee id to the filer_id that owns its filings.

    Accepts a ``cmte_id``/``xref_id`` (e.g. ``C0695132``, ``900532``) or a
    bare numeric ``filer_id``.  Returns ``-1`` when the id cannot be
    resolved; the filing subquery then matches nothing, which is the safe
    behaviour for an unknown committee.
    """
    rows = execute_read(
        "SELECT filer_id FROM filer_xref_cd WHERE xref_id = :cmte LIMIT 1",
        {"cmte": committee_id},
    )
    if rows:
        fid = rows[0].get("filer_id")
        if fid is not None:
            return int(fid)
    if committee_id.isdigit():
        return int(committee_id)
    return -1


def _committee_predicate(alias: str) -> str:
    """SQL predicate matching detail-table rows belonging to filer :filer.

    A row belongs to the committee iff the filing it was filed on belongs
    to the committee's filer (``filer_filings_cd``), resolved from the
    committee id via ``filer_xref_cd`` by the caller.

    The detail tables' own ``cmte_id`` column must NOT be used for this
    attribution: in the SOS export it names the *donor* committee on
    receipt lines and an unrelated cross-reference on expenditure lines
    (e.g. independent-expenditure committees carry a candidate's committee
    id on their lines), so matching it pulls in other committees' filings.
    """
    return (
        f"{alias}.filing_id IN ("
        "SELECT ff.filing_id FROM filer_filings_cd ff "
        "WHERE ff.filer_id = :filer)"
    )


def _committee_name(committee_id: str) -> dict[str, Any] | None:
    """Resolve a cmte_id (e.g. 'C0695132') to its filername_cd record.

    Uses the latest effective filer_xref_cd mapping.
    """
    rows = execute_read(
        """
        SELECT n.naml, n.namf, n.namt, n.nams, n.city, n.st,
               n.filer_type, n.status
        FROM filer_xref_cd x
        JOIN filername_cd n ON n.filer_id = x.filer_id
        WHERE x.xref_id = :cmte
        ORDER BY x.effect_dt DESC NULLS LAST
        LIMIT 1
        """,
        {"cmte": committee_id},
    )
    return rows[0] if rows else None


def _committee_display(row: dict[str, Any] | None) -> str:
    """Format a filername row as 'Last, First Middle Suffix' (best effort)."""
    if not row:
        return ""
    parts = [
        row.get("naml"),
        " ".join(p for p in (row.get("namf"), row.get("namt"), row.get("nams")) if p),
    ]
    return ", ".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------- #
#  Pydantic result models
# --------------------------------------------------------------------------- #


# -- 1. contributions_by_donor ----------------------------------------------- #


class ContributionRecord(BaseModel):
    tran_id: str | None = None
    filing_id: int | None = None
    amend_id: int | None = None
    cycle: int | None = None
    amount: float
    date: str | None = None
    purpose: str | None = None
    cmte_id: str | None = None
    memo_refno: str | None = None
    donor_name: str | None = None


# -- 2. top_donors_for_committee_or_candidate -------------------------------- #


class TopDonor(BaseModel):
    donor_name: str
    contributions: int
    total: float


# -- 3. committee_outlays_to -------------------------------------------------- #


class OutlayRecord(BaseModel):
    date: str | None = None
    amount: float
    purpose: str | None = None
    payee_name: str | None = None
    cmte_id: str | None = None
    tran_id: str | None = None
    memo_refno: str | None = None


# -- 4. vendor_revenue --------------------------------------------------------- #


class VendorRevenue(BaseModel):
    vendor_name: str
    payments: int
    total: float


# -- 4b. committees_paying_vendor --------------------------------------------- #


class VendorPayer(BaseModel):
    """A committee ranked by how much it paid a given vendor."""

    committee: str
    cmte_id: str | None = None
    filer_category: int | None = None
    is_candidate: bool = False
    payments: int
    total: float


# -- 5. committee_profile ------------------------------------------------------ #


class CommitteeProfile(BaseModel):
    committee_id: str
    committee_name: str | None = None
    committee_type: str | None = None
    status: str | None = None
    city: str | None = None
    state: str | None = None
    total_contributions: float
    contribution_count: int
    total_expenditures: float
    expenditure_count: int
    last_activity_date: str | None = None
    as_of_date: str | None = None


class CommitteeRef(BaseModel):
    """A committee matched by name, with the ID to pass to other tools."""

    cmte_id: str
    filer_id: int | None = None
    committee_name: str
    committee_type: str | None = None
    status: str | None = None
    city: str | None = None


# -- 6. measure_spending -------------------------------------------------------- #


class MeasureSpender(BaseModel):
    committee_name: str | None = None
    candidate: str | None = None
    sup_opp: str | None = None
    total: float
    filings: int


class MeasureSpending(BaseModel):
    measure_no: str | None = None
    measure_name: str | None = None
    measure_short_name: str | None = None
    election_date: str | None = None
    jurisdiction: str | None = None
    total_reported: float
    top_committees: list[MeasureSpender] = []


# -- 7. donor_watch_since -------------------------------------------------------- #


class DonorWatchRecord(BaseModel):
    tran_id: str | None = None
    filing_id: int | None = None
    amend_id: int | None = None
    amount: float
    date: str | None = None
    purpose: str | None = None
    cmte_id: str | None = None
    memo_refno: str | None = None
    donor_name: str | None = None


# -- 8. upcoming_filings ---------------------------------------------------------- #


class FilingDeadline(BaseModel):
    committee_id: str | None = None
    committee_name: str | None = None
    period_desc: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    deadline: str | None = None
    days_until: int | None = None


# -- 9. filing_due_soon ------------------------------------------------------------- #


class FilingDueSoon(BaseModel):
    report_type: str | None = None
    election_date: str | None = None
    deadline_date: str | None = None
    grace_period_days: int | None = None
    days_until: int | None = None
    source_url: str | None = None


# --------------------------------------------------------------------------- #
#  Tool implementations
# --------------------------------------------------------------------------- #


def contributions_by_donor(
    donor_name: str,
    cycle: int,
    include_aliases: bool = True,
) -> list[dict[str, Any]]:
    """Return all contributions by a donor name in a given election cycle.

    Args:
        donor_name: Partial or full donor name (``"Last, First M"`` or free
            text). Individuals match ``ctrib_naml``/``ctrib_namf``;
            organizations match ``ctrib_dscr``.
        cycle: Election cycle year (e.g. 2024) — derived from ``rcpt_date``.
        include_aliases: If True, also match alias names from the
            scraper-owned ``entity_alias`` table.

    Returns:
        List of ContributionRecord dicts (newest first, max 500).
    """
    p = _name_parts(donor_name)

    alias_clauses = ""
    extra_params: dict[str, Any] = {}
    if include_aliases:
        aliases = [a for a in _alias_names(p["base"]) if a.lower() != p["base"].lower()]
        if aliases:
            alias_clauses = " OR " + " OR ".join(
                f"r.ctrib_dscr = :alias{i}" for i in range(len(aliases))
            )
            for i, a in enumerate(aliases):
                extra_params[f"alias{i}"] = a

    sql = f"""
        SELECT
            r.tran_id,
            r.filing_id,
            r.amend_id,
            EXTRACT(YEAR FROM r.rcpt_date)::int AS cycle,
            r.amount,
            r.rcpt_date,
            r.ctrib_dscr AS purpose,
            r.cmte_id,
            r.memo_refno,
            COALESCE(
                NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
                r.ctrib_dscr
            ) AS donor_name
        FROM rcpt_cd r
        WHERE EXTRACT(YEAR FROM r.rcpt_date)::int = :cycle
          AND (
                r.ctrib_naml ILIKE :last
             OR (r.ctrib_naml ILIKE :last AND r.ctrib_namf ILIKE :first_mid)
             OR r.ctrib_dscr ILIKE :full
            {alias_clauses}
          )
        ORDER BY r.rcpt_date DESC
        LIMIT 500
    """
    rows = execute_read(
        sql,
        {"cycle": cycle, "last": p["last"], "first_mid": p["first_mid"],
         "full": p["full"], **extra_params},
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        rec = ContributionRecord(
            tran_id=c.get("tran_id"),
            filing_id=c.get("filing_id"),
            amend_id=c.get("amend_id"),
            cycle=c.get("cycle"),
            amount=_money(c.get("amount")),
            date=c.get("rcpt_date"),
            purpose=c.get("purpose"),
            cmte_id=c.get("cmte_id"),
            memo_refno=c.get("memo_refno"),
            donor_name=c.get("donor_name"),
        )
        out.append(rec.model_dump())
    return out


def top_donors_for_committee_or_candidate(
    committee_id: str,
    cycle: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the top N donors by total amount to a committee in a cycle.

    Args:
        committee_id: Committee ID as it appears on filings (``cmte_id``
            or ``xref_id``, e.g. ``C0695132``).
        cycle: Election cycle year (derived from ``rcpt_date``).
        limit: Maximum number of donors to return (default 10).

    Returns:
        List of TopDonor dicts sorted by total descending.
    """
    # For PAC / committee-to-committee receipts the SOS export leaves the
    # donor name fields blank and identifies the donor by committee ID in
    # rcpt_cd.cmte_id; resolve the name through filer_xref -> filername.
    # (filer_xref.xref_id is unique per filer, and donor_cmte is deduped to
    # one row per filer, so the joins cannot multiply receipt rows.)
    sql = f"""
        WITH donor_cmte AS (
            SELECT DISTINCT ON (filer_id) filer_id, naml, namf
            FROM filername_cd
        )
        SELECT
            COALESCE(
                NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
                r.ctrib_dscr,
                NULLIF(TRIM(COALESCE(dc.naml, '') || ' ' || COALESCE(dc.namf, '')), ''),
                '(unknown)'
            ) AS donor_name,
            COUNT(*) AS contributions,
            COALESCE(SUM(r.amount), 0) AS total
        FROM rcpt_cd r
        LEFT JOIN filer_xref_cd fx ON fx.xref_id = r.cmte_id
        LEFT JOIN donor_cmte dc ON dc.filer_id = fx.filer_id
        WHERE {_committee_predicate('r')}
          AND EXTRACT(YEAR FROM r.rcpt_date)::int = :cycle
        GROUP BY 1
        ORDER BY total DESC
        LIMIT :lim
    """
    rows = execute_read(
        sql,
        {"filer": _resolve_filer_id(committee_id),
         "cycle": cycle, "lim": limit},
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        rec = TopDonor(
            donor_name=c.get("donor_name") or "(unknown)",
            contributions=int(c.get("contributions") or 0),
            total=_money(c.get("total")),
        )
        out.append(rec.model_dump())
    return out


def committee_outlays_to(
    committee_id: str,
    vendor_name: str,
    cycle: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return expenditures from a committee to a vendor/payee in a cycle.

    Args:
        committee_id: Spending committee ID (``cmte_id`` or ``xref_id``,
            e.g. ``C0695132``).
        vendor_name: Payee/vendor name fragment (e.g. ``"Google"``); matched
            as a whole phrase anchored to a word boundary.
        cycle: Election cycle year (derived from ``expn_date``).
        limit: Maximum rows (default 50).

    Returns:
        List of OutlayRecord dicts (newest first).
    """
    sql = f"""
        SELECT
            e.expn_date,
            e.amount,
            e.expn_dscr AS purpose,
            COALESCE(
                NULLIF(TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, '')), ''),
                '(unknown payee)'
            ) AS payee_name,
            e.cmte_id,
            e.tran_id,
            e.memo_refno
        FROM expn_cd e
        WHERE {_committee_predicate('e')}
          AND EXTRACT(YEAR FROM e.expn_date)::int = :cycle
          AND TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, ''))
               ~* :vendor
        ORDER BY e.expn_date DESC
        LIMIT :lim
    """
    rows = execute_read(
        sql,
        {"filer": _resolve_filer_id(committee_id), "cycle": cycle, "lim": limit,
         "vendor": _vendor_regex(vendor_name)},
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        rec = OutlayRecord(
            date=c.get("expn_date"),
            amount=_money(c.get("amount")),
            purpose=c.get("purpose"),
            payee_name=c.get("payee_name"),
            cmte_id=c.get("cmte_id"),
            tran_id=c.get("tran_id"),
            memo_refno=c.get("memo_refno"),
        )
        out.append(rec.model_dump())
    return out


def vendor_revenue(vendor_name: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return total payments received by a vendor across all committees.

    The vendor is matched as a whole phrase anchored to a word boundary,
    so ``"AL Media"`` hits ``AL MEDIA`` / ``AL MEDIA LLC`` but not
    ``CENTRAL MEDIA``.

    Args:
        vendor_name: Payee/vendor name fragment (e.g. ``"Google"``).
        limit: Maximum rows (default 20).

    Returns:
        List of VendorRevenue dicts sorted by total descending.
    """
    sql = """
        SELECT
            COALESCE(
                NULLIF(TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, '')), ''),
                '(unknown payee)'
            ) AS vendor_name,
            COUNT(*) AS payments,
            COALESCE(SUM(e.amount), 0) AS total
        FROM expn_cd e
        WHERE TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, ''))
              ~* :vendor
        GROUP BY 1
        ORDER BY total DESC
        LIMIT :lim
    """
    rows = execute_read(
        sql,
        {"vendor": _vendor_regex(vendor_name), "lim": limit},
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        rec = VendorRevenue(
            vendor_name=c.get("vendor_name") or "(unknown payee)",
            payments=int(c.get("payments") or 0),
            total=_money(c.get("total")),
        )
        out.append(rec.model_dump())
    return out


def committees_paying_vendor(
    vendor_name: str,
    limit: int = 10,
    candidate_only: bool = False,
) -> list[dict[str, Any]]:
    """Rank the committees that paid a given vendor, by total amount.

    Vendor names are heavily fragmented across filings (e.g. 16+ spellings
    of "Google"), so the payee name is matched as a case-insensitive
    substring of the full payee name rather than exactly.

    Args:
        vendor_name: Vendor/payee name fragment (e.g. ``"Google"``,
            ``"AL Media"``, ``"The Trade Desk"``).
        limit: Maximum number of committees to return (default 10).
        candidate_only: When True, restrict to candidate committees
            (filer category ``40002``), excluding ballot-measure and other
            committee types.

    Returns:
        List of VendorPayer dicts sorted by total descending.
    """
    # Match the vendor as a whole phrase anchored to a word boundary so that
    # "AL Media" hits "AL MEDIA" / "AL MEDIA LLC" but NOT "CENTRAL MEDIA".
    vendor_regex = _vendor_regex(vendor_name)

    candidate_clause = "          AND fcl.category = 40002" if candidate_only else ""
    sql = f"""
        WITH filings AS (
            SELECT DISTINCT filing_id, filer_id FROM filer_filings_cd
        ),
        filer_name AS (
            SELECT DISTINCT ON (filer_id) filer_id, naml, namf
            FROM filername_cd ORDER BY filer_id
        ),
        filer_cmte AS (
            SELECT DISTINCT ON (filer_id) filer_id, xref_id
            FROM filer_xref_cd ORDER BY filer_id, effect_dt DESC NULLS LAST
        ),
        filer_class AS (
            SELECT DISTINCT ON (filer_id) filer_id, category
            FROM filer_to_filer_type_cd ORDER BY filer_id, effect_dt DESC NULLS LAST
        )
        SELECT
            COALESCE(
                NULLIF(TRIM(COALESCE(fn.naml, '') || ' ' || COALESCE(fn.namf, '')), ''),
                '(unknown)'
            ) AS committee,
            fc.xref_id AS cmte_id,
            fcl.category AS filer_category,
            COUNT(*) AS payments,
            COALESCE(SUM(e.amount), 0) AS total
        FROM expn_cd e
        JOIN filings ff ON ff.filing_id = e.filing_id
        LEFT JOIN filer_name fn ON fn.filer_id = ff.filer_id
        LEFT JOIN filer_cmte fc ON fc.filer_id = ff.filer_id
        LEFT JOIN filer_class fcl ON fcl.filer_id = ff.filer_id
        WHERE TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, ''))
              ~* :vendor{candidate_clause}
        GROUP BY 1, 2, 3
        ORDER BY total DESC
        LIMIT :lim
    """
    rows = execute_read(sql, {"vendor": vendor_regex, "lim": limit})
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        cat = c.get("filer_category")
        rec = VendorPayer(
            committee=c.get("committee") or "(unknown)",
            cmte_id=str(c["cmte_id"]) if c.get("cmte_id") else None,
            filer_category=cat,
            is_candidate=(cat == 40002),
            payments=int(c.get("payments") or 0),
            total=_money(c.get("total")),
        )
        out.append(rec.model_dump())
    return out


def committee_profile(
    committee_id: str,
    as_of_date: date | None = None,
) -> dict[str, Any] | None:
    """Return a summary profile for a committee.

    Args:
        committee_id: Committee ID (``cmte_id`` or ``xref_id``,
            e.g. ``C0695132``).
        as_of_date: If set, totals are computed over activity on or before
            this date.

    Returns:
        CommitteeProfile dict, or None if the committee ID is unknown.
    """
    name_row = _committee_name(committee_id)
    filer_id = _resolve_filer_id(committee_id)

    asof_clause_r = " AND r.rcpt_date <= :asof" if as_of_date else ""
    asof_clause_e = " AND e.expn_date <= :asof" if as_of_date else ""
    params_base: dict[str, Any] = {"filer": filer_id}
    if as_of_date:
        params_base["asof"] = as_of_date

    rcpt = execute_read(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n
        FROM rcpt_cd r
        WHERE {_committee_predicate('r')}{asof_clause_r}
        """,
        params_base,
    )[0]

    expn = execute_read(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n
        FROM expn_cd e
        WHERE {_committee_predicate('e')}{asof_clause_e}
        """,
        params_base,
    )[0]

    # Note: GREATEST() would return NULL when the committee has receipts
    # but no expenditures (or vice versa), so the two maxima are merged
    # with MAX() over a UNION ALL, which ignores NULLs.
    last_row = execute_read(
        f"""
        SELECT MAX(d) AS last_activity FROM (
            SELECT r.rcpt_date AS d FROM rcpt_cd r WHERE {_committee_predicate('r')}
            UNION ALL
            SELECT e.expn_date AS d FROM expn_cd e WHERE {_committee_predicate('e')}
        ) t
        """,
        {"filer": filer_id},
    )[0]

    rec = CommitteeProfile(
        committee_id=committee_id,
        committee_name=_committee_display(name_row) or None,
        committee_type=(name_row or {}).get("filer_type"),
        status=(name_row or {}).get("status"),
        city=(name_row or {}).get("city"),
        state=(name_row or {}).get("st"),
        total_contributions=_money(rcpt.get("total")),
        contribution_count=int(rcpt.get("n") or 0),
        total_expenditures=_money(expn.get("total")),
        expenditure_count=int(expn.get("n") or 0),
        last_activity_date=_coerce_row({"d": last_row.get("last_activity")}).get("d"),
        as_of_date=as_of_date.isoformat() if as_of_date else None,
    )
    return rec.model_dump()


def find_committees(name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find committees by (partial) name and return their IDs.

    Most of the other tools take a committee ID, which is awkward to
    guess; this tool bridges name -> ID.

    Args:
        name: Committee name fragment (case-insensitive substring,
            matched against the last/name and first-name columns).
        limit: Maximum number of committees to return (default 10).

    Returns:
        List of CommitteeRef dicts; ``cmte_id`` is the value to pass as
        ``committee_id`` to the other tools.
    """
    sql = """
        SELECT DISTINCT
            x.xref_id AS cmte_id,
            n.filer_id,
            n.naml AS committee_name,
            n.filer_type AS committee_type,
            n.status,
            n.city
        FROM filername_cd n
        JOIN filer_xref_cd x ON x.filer_id = n.filer_id
        WHERE n.naml ILIKE :q OR n.namf ILIKE :q
        ORDER BY n.naml
        LIMIT :lim
    """
    rows = execute_read(sql, {"q": f"%{name}%", "lim": limit})
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        cmte_id = c.get("cmte_id")
        if not cmte_id:
            continue
        rec = CommitteeRef(
            cmte_id=str(cmte_id),
            filer_id=c.get("filer_id"),
            committee_name=c.get("committee_name") or "",
            committee_type=c.get("committee_type"),
            status=c.get("status"),
            city=c.get("city"),
        )
        out.append(rec.model_dump())
    return out


def measure_spending(measure_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return spending/receipt totals reported for a ballot measure.

    Args:
        measure_id: Ballot measure number (``measure_no``/``bal_num``,
            e.g. ``114`` or ``AA``).
        limit: Max number of committees in ``top_committees`` (default 20).

    Returns:
        List with one MeasureSpending dict (empty list if the measure is
        not in ballot_measures_cd and no filings reference it).
    """
    meta_rows = execute_read(
        """
        SELECT measure_no, measure_name, measure_short_name,
               election_date, jurisdiction
        FROM ballot_measures_cd
        WHERE measure_no = :m
        ORDER BY election_date DESC
        LIMIT 1
        """,
        {"m": measure_id},
    )
    meta = meta_rows[0] if meta_rows else None

    spend_rows = execute_read(
        """
        SELECT
            COALESCE(
                NULLIF(TRIM(COALESCE(c.cand_naml, '') || ' ' || COALESCE(c.cand_namf, '')), ''),
                c.filer_naml,
                NULL
            ) AS candidate,
            COALESCE(
                NULLIF(TRIM(COALESCE(c.filer_naml, '') || ' ' || COALESCE(c.filer_namf, '')), ''),
                c.cand_naml,
                NULL
            ) AS committee_name,
            c.sup_opp_cd,
            COALESCE(SUM(s.amount_a), 0) AS total,
            COUNT(DISTINCT s.filing_id) AS filings
        FROM cvr_campaign_disclosure_cd c
        LEFT JOIN smry_cd s ON s.filing_id = c.filing_id
        WHERE c.bal_num = :m
        GROUP BY 1, 2, 3
        ORDER BY total DESC
        LIMIT :lim
        """,
        {"m": measure_id, "lim": limit},
    )

    spenders = [
        MeasureSpender(
            committee_name=(c := _coerce_row(r)).get("committee_name"),
            candidate=c.get("candidate"),
            sup_opp=c.get("sup_opp_cd"),
            total=_money(c.get("total")),
            filings=int(c.get("filings") or 0),
        ).model_dump()
        for r in spend_rows
    ]

    if meta is None and not spend_rows:
        return []

    m = _coerce_row(meta) if meta else {}
    rec = MeasureSpending(
        measure_no=m.get("measure_no") or measure_id,
        measure_name=m.get("measure_name"),
        measure_short_name=m.get("measure_short_name"),
        election_date=m.get("election_date"),
        jurisdiction=m.get("jurisdiction"),
        total_reported=sum(s["total"] for s in spenders),
        top_committees=spenders,
    )
    return [rec.model_dump()]


def donor_watch_since(
    donor_name: str,
    since_date: date,
    include_aliases: bool = True,
) -> list[dict[str, Any]]:
    """Return contributions from a donor on or after a given date.

    Args:
        donor_name: Donor name (``"Last, First M"`` or org name).
        since_date: Inclusive lower bound on ``rcpt_date``.
        include_aliases: If True, also match ``entity_alias`` names.

    Returns:
        List of DonorWatchRecord dicts (newest first, max 500).
    """
    p = _name_parts(donor_name)

    alias_clauses = ""
    extra_params: dict[str, Any] = {}
    if include_aliases:
        aliases = [a for a in _alias_names(p["base"]) if a.lower() != p["base"].lower()]
        if aliases:
            alias_clauses = " OR " + " OR ".join(
                f"r.ctrib_dscr = :alias{i}" for i in range(len(aliases))
            )
            for i, a in enumerate(aliases):
                extra_params[f"alias{i}"] = a

    sql = f"""
        SELECT
            r.tran_id,
            r.filing_id,
            r.amend_id,
            r.amount,
            r.rcpt_date,
            r.ctrib_dscr AS purpose,
            r.cmte_id,
            r.memo_refno,
            COALESCE(
                NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
                r.ctrib_dscr
            ) AS donor_name
        FROM rcpt_cd r
        WHERE r.rcpt_date >= :since
          AND (
                r.ctrib_naml ILIKE :last
             OR (r.ctrib_naml ILIKE :last AND r.ctrib_namf ILIKE :first_mid)
             OR r.ctrib_dscr ILIKE :full
            {alias_clauses}
          )
        ORDER BY r.rcpt_date DESC
        LIMIT 500
    """
    rows = execute_read(
        sql,
        {"since": since_date, "last": p["last"], "first_mid": p["first_mid"],
         "full": p["full"], **extra_params},
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        rec = DonorWatchRecord(
            tran_id=c.get("tran_id"),
            filing_id=c.get("filing_id"),
            amend_id=c.get("amend_id"),
            amount=_money(c.get("amount")),
            date=c.get("rcpt_date"),
            purpose=c.get("purpose"),
            cmte_id=c.get("cmte_id"),
            memo_refno=c.get("memo_refno"),
            donor_name=c.get("donor_name"),
        )
        out.append(rec.model_dump())
    return out


def upcoming_filings(
    committee_id: str,
    days_ahead: int = 30,
) -> list[dict[str, Any]]:
    """Return filing deadlines falling within the next N days.

    Deadlines come from the CAL-ACCESS ``filing_period_cd`` table (per
    reporting period type). The committee is annotated with its resolved
    name; CAL-ACCESS period rows are not committee-specific, so the same
    period deadlines apply to every reporting committee.

    Args:
        committee_id: Committee ID (``cmte_id``, e.g. ``C0695132``).
        days_ahead: Number of days to look ahead (default 30).

    Returns:
        List of FilingDeadline dicts ordered by deadline ascending.
    """
    today = date.today()
    future = today + timedelta(days=days_ahead)
    name = _committee_name(committee_id)

    rows = execute_read(
        """
        SELECT period_id, period_desc, start_date, end_date, deadline
        FROM filing_period_cd
        WHERE deadline >= :today AND deadline <= :future
        ORDER BY deadline ASC
        """,
        {"today": today, "future": future},
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        dl = c.get("deadline")
        days_until = None
        if dl:
            dl_date = (
                dl.date() if isinstance(dl, datetime)
                else (dl if isinstance(dl, date) else date.fromisoformat(dl[:10]))
            )
            days_until = (dl_date - today).days
        rec = FilingDeadline(
            committee_id=committee_id,
            committee_name=_committee_display(name),
            period_desc=c.get("period_desc"),
            start_date=c.get("start_date"),
            end_date=c.get("end_date"),
            deadline=dl,
            days_until=days_until,
        )
        out.append(rec.model_dump())
    return out


def filing_due_soon(days_ahead: int = 7) -> list[dict[str, Any]]:
    """Return scraper-tracked filing deadlines due within N days.

    Data comes from the scraper-owned ``filing_calendar`` table
    (``election_date``, ``report_type``, ``deadline_date``,
    ``grace_period_days``). Until the filing-calendar scraper runs, this
    table is empty and the tool returns [].

    Args:
        days_ahead: Number of days to look ahead (default 7).

    Returns:
        List of FilingDueSoon dicts ordered by deadline ascending.
    """
    today = date.today()
    future = today + timedelta(days=days_ahead)

    rows = execute_read(
        """
        SELECT report_type, election_date, deadline_date,
               grace_period_days, source_url
        FROM filing_calendar
        WHERE deadline_date >= :today AND deadline_date <= :future
        ORDER BY deadline_date ASC
        """,
        {"today": today, "future": future},
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_row(row)
        dl = c.get("deadline_date")
        days_until = None
        if dl:
            dl_date = (
                dl.date() if isinstance(dl, datetime)
                else (dl if isinstance(dl, date) else date.fromisoformat(dl[:10]))
            )
            days_until = (dl_date - today).days
        rec = FilingDueSoon(
            report_type=c.get("report_type"),
            election_date=c.get("election_date"),
            deadline_date=dl,
            grace_period_days=c.get("grace_period_days"),
            days_until=days_until,
            source_url=c.get("source_url"),
        )
        out.append(rec.model_dump())
    return out
