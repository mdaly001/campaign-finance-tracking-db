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
- Contribution tools read the ``receipts_all`` view, which unions
  ``rcpt_cd`` (periodic reports) with the rapid-disclosure tables
  ``s497_cd`` (Form 497 24-hour large-contribution reports) and
  ``s498_cd`` (Form 498 receipts). A gift disclosed in a 24-hour report is
  usually disclosed again in a later periodic report, so the tools de-dup
  across sources before counting (see the de-dup note above the helpers).

Each tool returns structured data via Pydantic models that map to
JSON-serializable dictionaries for the MCP transport layer.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from core.mcp.db import execute_read, get_engine

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


def _person_predicate(
    naml_field: str, namf_field: str, name: str, prefix: str = "pl"
) -> tuple[str, dict[str, Any]]:
    """SQL predicate + params matching a *person* name across the two
    name fields of one alias row.

    Handles every storage order seen in the SOS export:

    - last-first:  ``naml='Daly'``, ``namf='Michael Gomez'``
    - first-last:  ``naml='Michael'``, ``namf='Daly'``
    - single field: ``naml='Gomez Daly'``, ``namf=''`` (or the reverse)

    Matches are word-anchored (``\\m``, Postgres ARE flavor) so searching
    "Daly" never hits ``Odalys`` or ``BRENDALYN``, and a middle name is
    optional (searching "Michael Gomez Daly" still finds "Daly Michael").
    Used with the case-insensitive ``~*`` operator.
    """
    p = _name_parts(name)
    last = p["last"].strip("%").strip()
    first = p["first_mid"].strip().rstrip("%").strip()
    first = first.split()[0] if first else ""  # middle initials optional

    last_r, first_r = re.escape(last), re.escape(first)
    naml, namf = naml_field, namf_field
    comb = (
        f"TRIM(COALESCE({naml}, '') || ' ' || COALESCE({namf}, ''))"
    )
    params: dict[str, Any] = {}
    branches: list[str] = []
    if last and first:
        branches.append(f"({naml} ~* :{prefix}_last AND {namf} ~* :{prefix}_first)")
        branches.append(f"({naml} ~* :{prefix}_first AND {namf} ~* :{prefix}_last)")
        branches.append(f"({comb} ~* :{prefix}_lastfirst)")
        branches.append(f"({comb} ~* :{prefix}_firstlast)")
        params = {
            f"{prefix}_last": rf"\m{last_r}",
            f"{prefix}_first": rf"\m{first_r}",
            f"{prefix}_lastfirst": rf"\m{last_r}\b[\s,.]+{first_r}",
            f"{prefix}_firstlast": rf"\m{first_r}(?:[\s.][^.]*[\s.])*{last_r}\b",
        }
    elif last:
        branches.append(f"({naml} ~* :{prefix}_last)")
        branches.append(f"({namf} ~* :{prefix}_last)")
        params = {f"{prefix}_last": rf"\m{last_r}"}
    else:
        branches.append(f"({naml} ~* :{prefix}_first)")
        branches.append(f"({namf} ~* :{prefix}_first)")
        params = {f"{prefix}_first": rf"\m{first_r}"}
    return "(" + " OR ".join(branches) + ")", params


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


# --------------------------------------------------------------------------- #
#  Receipts union + 24-hour de-duplication
# --------------------------------------------------------------------------- #
#
# Contributions are disclosed in three detail tables: rcpt_cd (periodic
# reports), s497_cd (Form 497 24-hour large-contribution reports) and
# s498_cd (Form 498 rapid-disclosure receipts). A gift that triggered a
# 24-hour report is usually reported AGAIN in a later periodic report, so
# the same contribution can appear in more than one table.
#
# The ``receipts_all`` view (migrations/0002) normalizes the three tables
# into one row layout. The de-dup below runs on the *filtered* subset of
# that view: for each (donor, date, amount) group it keeps
# ``max(rows-per-source)`` rows, preferring rcpt_cd rows first, so that
#
#   * a gift reported in both a 24-hour and a periodic report counts once,
#   * two different gifts with the same date+amount on the same day both
#     survive (the group keeps the max of the per-source counts, not the
#     min — a source that reported the day more completely wins).
#
# Known limitation: rows are matched by donor name (or donor cmte_id when
# the name is blank). A committee donor identified by name in one source
# and by cmte_id in the other is not merged and can be counted twice.


_SRC_PRIORITY = "CASE src WHEN 'rcpt_cd' THEN 1 WHEN 's497_cd' THEN 2 ELSE 3 END"


def _donor_match_predicate(alias: str, alias_clauses: str) -> str:
    """WHERE fragment matching a donor by name across all receipt sources.

    Individuals match ``donor_naml``/``donor_namf`` (s497/s498 store org
    names in the same "name last" column, so they match too); organizations
    also match ``ctrib_dscr`` (rcpt_cd only) and entity_alias names.
    """
    return (
        f"({alias}.donor_naml ILIKE :last"
        f" OR ({alias}.donor_naml ILIKE :last"
        f" AND {alias}.donor_namf ILIKE :first_mid)"
        f" OR {alias}.donor_naml ILIKE :full"
        f" OR {alias}.ctrib_dscr ILIKE :full"
        f"{alias_clauses})"
    )


def _alias_clauses(alias: str, p: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Build entity_alias match clauses for the receipts view."""
    extra: dict[str, Any] = {}
    aliases = [
        a for a in _alias_names(p["base"]) if a.lower() != p["base"].lower()
    ]
    if not aliases:
        return "", extra
    clause = " OR " + " OR ".join(
        f"({alias}.ctrib_dscr = :alias{i} OR {alias}.donor_naml = :alias{i})"
        for i in range(len(aliases))
    )
    for i, a in enumerate(aliases):
        extra[f"alias{i}"] = a
    return clause, extra


def _rowlist_dedup_sql(
    row_cte: str, select_sql: str, order_by: str, limit: int | str
) -> str:
    """Wrap a filtered row CTE (named ``r``) in the cross-source de-dup.

    ``row_cte`` must select the normalized ``receipts_all`` columns
    (``src, filing_id, tran_id, receipt_date, amount, donor_key,
    donor_name, ...``) already restricted to the donor/cycle/date of
    interest. ``select_sql`` is the final SELECT list (prefix columns with
    ``s.``); the result rows come from ``seq s`` joined to the ``keep``
    CTE. See the module de-dup note above for the semantics.
    """
    return f"""
    WITH {row_cte},
    seq AS (
        SELECT r.*,
               ROW_NUMBER() OVER (
                   PARTITION BY donor_key, receipt_date, amount
                   ORDER BY {_SRC_PRIORITY},
                            tran_id NULLS LAST,
                            filing_id
               ) AS rn
        FROM r
    ),
    keep AS (
        SELECT donor_key, receipt_date, amount, MAX(src_n) AS keep_n
        FROM (
            SELECT donor_key, receipt_date, amount, src, COUNT(*) AS src_n
            FROM r
            GROUP BY 1, 2, 3, 4
        ) p
        GROUP BY 1, 2, 3
    )
    {select_sql}
    FROM seq s
    JOIN keep k
      ON k.donor_key = s.donor_key
     AND k.receipt_date = s.receipt_date
     AND k.amount = s.amount
    WHERE s.rn <= k.keep_n
    ORDER BY {order_by}
    LIMIT {limit}
    """


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
    source: str | None = None  # receipts_all source: rcpt_cd | s497_cd | s498_cd


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
    source: str | None = None  # receipts_all source: rcpt_cd | s497_cd | s498_cd


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

    Covers periodic reports AND the 24-hour Form 497 / Form 498 rapid
    reports (via the ``receipts_all`` view); a gift disclosed in both is
    counted once (see the de-dup note above the helpers). Each row's
    ``source`` names the table it came from.

    Args:
        donor_name: Partial or full donor name (``"Last, First M"`` or free
            text). Individuals match the donor last/first-name fields;
            organizations match the donor/org name and description fields.
        cycle: Election cycle year (e.g. 2024) — derived from the receipt
            date.
        include_aliases: If True, also match alias names from the
            scraper-owned ``entity_alias`` table.

    Returns:
        List of ContributionRecord dicts (newest first, max 500).
    """
    p = _name_parts(donor_name)
    alias_clauses, extra_params = (
        _alias_clauses("x", p) if include_aliases else ("", {})
    )

    row_cte = f"""
        r AS (
            SELECT x.src, x.filing_id, x.amend_id, x.tran_id, x.receipt_date,
                   x.amount, x.ctrib_dscr, x.cmte_id, x.memo_refno,
                   x.donor_key, x.donor_name
            FROM receipts_all x
            WHERE EXTRACT(YEAR FROM x.receipt_date)::int = :cycle
              AND {_donor_match_predicate('x', alias_clauses)}
        )
    """
    select_sql = """
        SELECT
            s.src AS source,
            s.tran_id,
            s.filing_id,
            s.amend_id,
            EXTRACT(YEAR FROM s.receipt_date)::int AS cycle,
            s.amount,
            s.receipt_date AS rcpt_date,
            s.ctrib_dscr AS purpose,
            s.cmte_id,
            s.memo_refno,
            COALESCE(NULLIF(TRIM(s.donor_name), ''), s.cmte_id) AS donor_name
    """
    sql = _rowlist_dedup_sql(row_cte, select_sql, "s.receipt_date DESC", 500)
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
            source=c.get("source"),
        )
        out.append(rec.model_dump())
    return out


def top_donors_for_committee_or_candidate(
    committee_id: str,
    cycle: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the top N donors by total amount to a committee in a cycle.

    Covers periodic reports AND the 24-hour Form 497 / Form 498 rapid
    reports (via the ``receipts_all`` view); a gift disclosed in both is
    counted once (see the de-dup note above the helpers).

    Args:
        committee_id: Committee ID as it appears on filings (``cmte_id``
            or ``xref_id``, e.g. ``C0695132``).
        cycle: Election cycle year (derived from the receipt date).
        limit: Maximum number of donors to return (default 10).

    Returns:
        List of TopDonor dicts sorted by total descending.
    """
    # For PAC / committee-to-committee receipts the SOS export leaves the
    # donor name fields blank and identifies the donor by committee ID in
    # cmte_id; resolve the name through filer_xref -> filername.
    # (xref_id is unique in filer_xref_cd, and donor_cmte is deduped to one
    # row per filer, so the joins cannot multiply receipt rows.)
    #
    # The r/dedup CTEs implement the cross-source de-dup: rows are grouped
    # per (donor, date, amount, source) and the group keeps the MAX
    # per-source row count, so double-reported gifts count once.
    sql = f"""
        WITH donor_cmte AS (
            SELECT DISTINCT ON (filer_id) filer_id, naml, namf
            FROM filername_cd
        ),
        r AS (
            SELECT x.donor_key, x.donor_name, x.cmte_id,
                   x.receipt_date, x.amount, x.src,
                   COUNT(*) AS src_n
            FROM receipts_all x
            WHERE {_committee_predicate('x')}
              AND EXTRACT(YEAR FROM x.receipt_date)::int = :cycle
            GROUP BY x.donor_key, x.donor_name, x.cmte_id,
                     x.receipt_date, x.amount, x.src
        ),
        dedup AS (
            SELECT donor_key,
                   MAX(NULLIF(donor_name, '')) AS donor_name,
                   MAX(cmte_id) AS cmte_id,
                   receipt_date,
                   amount,
                   MAX(src_n) AS keep_n
            FROM r
            GROUP BY donor_key, receipt_date, amount
        )
        SELECT
            COALESCE(
                NULLIF(TRIM(dedup.donor_name), ''),
                NULLIF(TRIM(COALESCE(dc.naml, '') || ' ' || COALESCE(dc.namf, '')), ''),
                dedup.cmte_id,
                '(unknown)'
            ) AS donor_name,
            SUM(keep_n) AS contributions,
            SUM(keep_n * amount) AS total
        FROM dedup
        LEFT JOIN filer_xref_cd fx ON fx.xref_id = dedup.cmte_id
        LEFT JOIN donor_cmte dc ON dc.filer_id = fx.filer_id
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
        FROM expn_cd_deduped e
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
        FROM expn_cd_deduped e
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
        FROM expn_cd_deduped e
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

    Contribution totals include the 24-hour Form 497 / Form 498 rapid
    reports (via the ``receipts_all`` view); double-reported gifts are
    counted once (see the de-dup note above the helpers).

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

    asof_clause_r = " AND x.receipt_date <= :asof" if as_of_date else ""
    asof_clause_e = " AND e.expn_date <= :asof" if as_of_date else ""
    params_base: dict[str, Any] = {"filer": filer_id}
    if as_of_date:
        params_base["asof"] = as_of_date

    # receipts_all = rcpt_cd + 24-hr s497_cd + s498_cd; the dedup CTE keeps
    # double-reported gifts counted once (max rows per donor+date+amount
    # across sources).
    rcpt = execute_read(
        f"""
        WITH r AS (
            SELECT x.donor_key, x.receipt_date, x.amount, x.src,
                   COUNT(*) AS src_n
            FROM receipts_all x
            WHERE {_committee_predicate('x')}{asof_clause_r}
            GROUP BY 1, 2, 3, 4
        ),
        dedup AS (
            SELECT donor_key, receipt_date, amount, MAX(src_n) AS keep_n
            FROM r
            GROUP BY 1, 2, 3
        )
        SELECT COALESCE(SUM(keep_n * amount), 0) AS total,
               COALESCE(SUM(keep_n), 0) AS n
        FROM dedup
        """,
        params_base,
    )[0]

    expn = execute_read(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n
        FROM expn_cd_deduped e
        WHERE {_committee_predicate('e')}{asof_clause_e}
        """,
        params_base,
    )[0]

    # Note: GREATEST() would return NULL when the committee has receipts
    # but no expenditures (or vice versa), so the two maxima are merged
    # with MAX() over a UNION ALL, which ignores NULLs. The receipts branch
    # uses receipts_all so 24-hour report activity counts as activity.
    last_row = execute_read(
        f"""
        SELECT MAX(d) AS last_activity FROM (
            SELECT x.receipt_date AS d FROM receipts_all x WHERE {_committee_predicate('x')}
            UNION ALL
            SELECT e.expn_date AS d FROM expn_cd_deduped e WHERE {_committee_predicate('e')}
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

    Covers periodic reports AND the 24-hour Form 497 / Form 498 rapid
    reports (via the ``receipts_all`` view); a gift disclosed in both is
    counted once (see the de-dup note above the helpers). Each row's
    ``source`` names the table it came from.

    Args:
        donor_name: Donor name (``"Last, First M"`` or org name).
        since_date: Inclusive lower bound on the receipt date.
        include_aliases: If True, also match ``entity_alias`` names.

    Returns:
        List of DonorWatchRecord dicts (newest first, max 500).
    """
    p = _name_parts(donor_name)
    alias_clauses, extra_params = (
        _alias_clauses("x", p) if include_aliases else ("", {})
    )

    row_cte = f"""
        r AS (
            SELECT x.src, x.filing_id, x.amend_id, x.tran_id, x.receipt_date,
                   x.amount, x.ctrib_dscr, x.cmte_id, x.memo_refno,
                   x.donor_key, x.donor_name
            FROM receipts_all x
            WHERE x.receipt_date >= :since
              AND {_donor_match_predicate('x', alias_clauses)}
        )
    """
    select_sql = """
        SELECT
            s.src AS source,
            s.tran_id,
            s.filing_id,
            s.amend_id,
            s.amount,
            s.receipt_date AS rcpt_date,
            s.ctrib_dscr AS purpose,
            s.cmte_id,
            s.memo_refno,
            COALESCE(NULLIF(TRIM(s.donor_name), ''), s.cmte_id) AS donor_name
    """
    sql = _rowlist_dedup_sql(row_cte, select_sql, "s.receipt_date DESC", 500)
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
            source=c.get("source"),
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


# --------------------------------------------------------------------------- #
#  12. payments_to_person
#  13. rapid_expense_vendors
#  14. describe_table
#  15. get_server_docs
# --------------------------------------------------------------------------- #


class PersonPayment(BaseModel):
    """A payment made TO the person (payee in expn_cd)."""

    date: str | None = None
    amount: float
    purpose: str | None = None
    payee_name: str | None = None
    committee: str | None = None
    cmte_id: str | None = None


class PersonGift(BaseModel):
    """A contribution made BY the person (via receipts_all, de-duped)."""

    date: str | None = None
    amount: float
    purpose: str | None = None
    donor_name: str | None = None
    cmte_id: str | None = None
    source: str | None = None


class PersonFiler(BaseModel):
    """A committee/candidate whose name matches the person."""

    filer_id: int
    name: str
    cmte_id: str | None = None


class PersonBlindSpot(BaseModel):
    """Form 496 24-hour lines of the paying committees that vendor
    queries cannot see (no payee name field)."""

    s496_lines_for_paying_committees: int
    note: str = (
        "These 24-hour expenditure lines have no payee name in the SOS "
        "export; some may be additional payments to the person. Use "
        "rapid_expense_vendors on those committees to resolve them."
    )


def payments_to_person(
    person_name: str,
    since_date: date | None = None,
    roles: str = "all",
    limit: int = 100,
) -> dict[str, Any]:
    """Find every role a person plays in the disclosure data.

    "Payments to X" is role-ambiguous, so this searches all three at once
    and labels each hit:

    - ``payee`` — payments made TO the person (``expn_cd`` payee lines,
      i.e. the person as vendor/consultant),
    - ``donor`` — contributions made BY the person (``receipts_all``,
      24-hour de-duplicated),
    - ``filer`` — committees/candidates whose name matches (the person as
      a campaigner).

    Name matching is field-aware and word-anchored (see
    ``_person_predicate``): it finds last-first and first-last storage and
    ignores substring false friends like "Daly" inside "Odalys".

    Args:
        person_name: Person name (``"Michael Gomez Daly"``,
            ``"Daly, Michael"``, or just ``"Daly"``).
        since_date: Inclusive lower bound on transaction date (all roles).
        roles: ``"all"`` (default) or a single role ``"payee"``,
            ``"donor"``, ``"filer"``.
        limit: Maximum detail rows per role (default 100).

    Returns:
        Dict with per-role match counts (``total`` is the pre-limit count),
        detail rows (newest first), and — when the person was paid — a
        ``blind_spot`` count of the paying committees' Form 496 lines,
        which carry no payee name and may include further payments.
    """
    roles_wanted = {r.strip().lower() for r in roles.split(",")} or {"all"}
    do_payee = roles_wanted <= {"all"} or "payee" in roles_wanted
    do_donor = roles_wanted <= {"all"} or "donor" in roles_wanted
    do_filer = roles_wanted <= {"all"} or "filer" in roles_wanted

    since_sql = " AND {a}.expn_date >= :since" if since_date else ""
    result: dict[str, Any] = {"person": person_name, "since": _dtos(since_date)}

    # -- payee: payments made TO the person ----------------------------- #
    pred, params = _person_predicate(
        "e.payee_naml", "e.payee_namf", person_name, prefix="pe"
    )
    payee_rows: list[dict[str, Any]] = []
    payee_total = 0
    if do_payee:
        payee_sql = f"""
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
            m AS (
                SELECT e.filing_id, e.expn_date, e.amount, e.expn_dscr,
                       e.payee_naml, e.payee_namf, ff.filer_id,
                       COUNT(*) OVER () AS total_matches
                FROM expn_cd_deduped e
                JOIN filings ff ON ff.filing_id = e.filing_id
                WHERE {pred}{since_sql.format(a='e')}
            )
            SELECT m.expn_date, m.amount,
                   LEFT(TRIM(COALESCE(m.expn_dscr, '')), 120) AS purpose,
                   TRIM(COALESCE(m.payee_naml, '') || ' ' || COALESCE(m.payee_namf, ''))
                       AS payee_name,
                   m.filer_id,
                   TRIM(COALESCE(fn.naml, '') || ' ' || COALESCE(fn.namf, ''))
                       AS committee,
                   fc.xref_id AS cmte_id,
                   m.total_matches
            FROM m
            LEFT JOIN filer_name fn ON fn.filer_id = m.filer_id
            LEFT JOIN filer_cmte fc ON fc.filer_id = m.filer_id
            ORDER BY m.expn_date DESC NULLS LAST
            LIMIT :lim
        """
        rows = execute_read(
            payee_sql,
            {"lim": limit, **params, **({"since": since_date} if since_date else {})},
        )
        for row in rows:
            c = _coerce_row(row)
            payee_total = int(c.get("total_matches") or 0)
            rec = PersonPayment(
                date=c.get("expn_date"),
                amount=_money(c.get("amount")),
                purpose=c.get("purpose"),
                payee_name=c.get("payee_name") or None,
                committee=c.get("committee") or None,
                cmte_id=str(c["cmte_id"]) if c.get("cmte_id") else None,
            )
            payee_rows.append(rec.model_dump())
        result["payee"] = {
            "total": payee_total,
            "returned": len(payee_rows),
            "payments": payee_rows,
        }

        # blind spot: Form 496 lines of the paying committees
        if payee_total > 0:
            s496_since = " AND s.exp_date >= :s496_since" if since_date else ""
            bs = execute_read(
                f"""
                SELECT COUNT(*) AS n
                FROM s496_cd_deduped s
                WHERE s.filing_id IN (
                    SELECT DISTINCT ff.filing_id
                    FROM filer_filings_cd ff
                    WHERE ff.filer_id IN (
                        SELECT DISTINCT ff2.filer_id
                        FROM expn_cd_deduped e2
                        JOIN filer_filings_cd ff2 ON ff2.filing_id = e2.filing_id
                        WHERE {pred.replace('e.', 'e2.')}
                        {since_sql.format(a='e2')}
                    )
                ){s496_since}
                """,
                {**params,
                 **({"since": since_date, "s496_since": since_date}
                    if since_date else {})},
            )
            result["blind_spot"] = PersonBlindSpot(
                s496_lines_for_paying_committees=int(bs[0]["n"]) if bs else 0
            ).model_dump()

    # -- donor: contributions made BY the person ------------------------- #
    if do_donor:
        dpred, dparams = _person_predicate(
            "x.donor_naml", "x.donor_namf", person_name, prefix="pd"
        )
        dsince = " AND x.receipt_date >= :since" if since_date else ""
        row_cte = f"""
            r AS (
                SELECT x.src, x.filing_id, x.amend_id, x.tran_id,
                       x.receipt_date, x.amount, x.ctrib_dscr, x.cmte_id,
                       x.memo_refno, x.donor_key, x.donor_name
                FROM receipts_all x
                WHERE {dpred}{dsince}
            )
        """
        select_sql = """
            SELECT s.src AS source, s.tran_id, s.filing_id, s.amend_id,
                   s.amount, s.receipt_date AS rcpt_date,
                   s.ctrib_dscr AS purpose, s.cmte_id, s.memo_refno,
                   COALESCE(NULLIF(TRIM(s.donor_name), ''), s.cmte_id)
                       AS donor_name
        """
        dtotal_rows = execute_read(
            f"SELECT COUNT(*) AS n FROM receipts_all x WHERE {dpred}{dsince}",
            {**dparams, **({"since": since_date} if since_date else {})},
        )
        donor_total = int(dtotal_rows[0]["n"]) if dtotal_rows else 0
        sql = _rowlist_dedup_sql(
            row_cte, select_sql, "s.receipt_date DESC NULLS LAST", 500
        )
        rows = execute_read(
            sql, {**dparams, **({"since": since_date} if since_date else {})}
        )
        gift_rows: list[dict[str, Any]] = []
        for row in rows:
            c = _coerce_row(row)
            rec = PersonGift(
                date=c.get("rcpt_date"),
                amount=_money(c.get("amount")),
                purpose=c.get("purpose"),
                donor_name=c.get("donor_name"),
                cmte_id=c.get("cmte_id"),
                source=c.get("source"),
            )
            gift_rows.append(rec.model_dump())
        result["donor"] = {
            "total": donor_total,
            "returned": len(gift_rows),
            "gifts": gift_rows[:limit],
        }

    # -- filer: committees/candidates with this name --------------------- #
    if do_filer:
        fpred, fparams = _person_predicate(
            "fn.naml", "fn.namf", person_name, prefix="pf"
        )
        rows = execute_read(
            f"""
            SELECT DISTINCT fn.filer_id,
                   TRIM(COALESCE(fn.naml, '') || ' ' || COALESCE(fn.namf, ''))
                       AS name,
                   fc.xref_id AS cmte_id
            FROM filername_cd fn
            LEFT JOIN (
                SELECT DISTINCT ON (filer_id) filer_id, xref_id
                FROM filer_xref_cd
                ORDER BY filer_id, effect_dt DESC NULLS LAST
            ) fc ON fc.filer_id = fn.filer_id
            WHERE {fpred}
            ORDER BY fn.filer_id
            LIMIT :lim
            """,
            {"lim": limit, **fparams},
        )
        filer_rows = [
            PersonFiler(
                filer_id=int(c.get("filer_id")),
                name=c.get("name") or "(unknown)",
                cmte_id=str(c["cmte_id"]) if c.get("cmte_id") else None,
            ).model_dump()
            for c in (_coerce_row(r) for r in rows)
        ]
        result["filer"] = {"matches": filer_rows}

    return result


class RapidExpenseLine(BaseModel):
    """One Form 496 (24-hour) expenditure line, with its payee resolved
    from the periodic re-report when possible."""

    date: str | None = None
    amount: float
    description: str | None = None
    payee: str | None = None  # None => unresolved (see note)
    resolved: bool = False
    ambiguous: bool = False  # True => several payees match (date, amount)


def rapid_expense_vendors(
    committee_id: str,
    since_date: date | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Resolve the payees of a committee's 24-hour (Form 496) expenditures.

    Form 496 lines carry amount, date and a free-text description but NO
    payee name (structural blind spot). Periodic reports re-file the same
    dollars to the exact cent with payee names, so a (payment date, amount)
    match within the same filer recovers the vendor for most lines
    (80-97% measured on the largest rapid-disclosure filers). Unresolved
    lines are usually recent spend whose periodic re-report has not been
    filed yet.

    Args:
        committee_id: Committee ID (``cmte_id``/``xref_id`` or a numeric
            ``filer_id``).
        since_date: Inclusive lower bound on the payment date.
        limit: Maximum lines to return (default 200).

    Returns:
        Dict with ``total_lines`` (pre-limit), per-line payees (resolved
        and unresolved, newest first), ``resolution_pct``, and a note.
    """
    fid = _resolve_filer_id(committee_id)
    if fid < 0:
        return {
            "committee_id": committee_id,
            "committee": None,
            "total_lines": 0,
            "returned": 0,
            "resolved": [],
            "unresolved": [],
            "resolution_pct": 0.0,
            "note": "committee id could not be resolved; no filings matched.",
        }

    since_sql = " AND s.exp_date >= :since" if since_date else ""
    total_rows = execute_read(
        f"""
        SELECT COUNT(*) AS n
        FROM s496_cd_deduped s
        WHERE s.filing_id IN (
            SELECT DISTINCT filing_id FROM filer_filings_cd WHERE filer_id = :fid
        ){since_sql}
        """,
        {"fid": fid, **({"since": since_date} if since_date else {})},
    )
    total = int(total_rows[0]["n"]) if total_rows else 0

    # One row per 24-hour line (no fan-out): matched payees are collected
    # into an array so a (date, amount) matching several payees still
    # yields exactly one line, flagged ambiguous.
    rows = execute_read(
        f"""
        WITH f AS (
            SELECT DISTINCT filing_id FROM filer_filings_cd
            WHERE filer_id = :fid
        ),
        s496 AS (
            SELECT s.filing_id, s.line_item,
                   s.exp_date::date AS d, s.amount,
                   LEFT(TRIM(COALESCE(s.expn_dscr, '')), 120) AS dscr
            FROM s496_cd_deduped s
            WHERE s.filing_id IN (SELECT filing_id FROM f){since_sql}
        ),
        expn AS (
            SELECT DISTINCT e.expn_date::date AS d, e.amount,
                   TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, ''))
                       AS payee
            FROM expn_cd_deduped e
            WHERE e.filing_id IN (SELECT filing_id FROM f)
              AND TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, ''))
                  <> ''
        )
        SELECT s.d, s.amount, s.dscr,
               (SELECT ARRAY_AGG(p.payee ORDER BY p.payee)
                FROM expn p
                WHERE p.d = s.d AND p.amount = s.amount
               ) AS payees
        FROM s496 s
        ORDER BY s.d DESC NULLS LAST, s.filing_id, s.line_item
        LIMIT :lim
        """,
        {"fid": fid, "lim": limit, **({"since": since_date} if since_date else {})},
    )

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    n_resolved = 0
    for row in rows:
        c = _coerce_row(row)
        payees: list[str] = [p for p in (c.get("payees") or []) if p]
        rec = RapidExpenseLine(
            date=c.get("d"),
            amount=_money(c.get("amount")),
            description=c.get("dscr") or None,
            payee=payees[0] if payees else None,
            resolved=bool(payees),
            ambiguous=len(payees) > 1,
        )
        if rec.resolved:
            n_resolved += 1
            resolved.append(rec.model_dump())
        else:
            unresolved.append(rec.model_dump())

    name = _committee_name(committee_id)
    returned = len(resolved) + len(unresolved)
    # When truncated, the pct is a floor (only over the returned rows).
    pct = round(100.0 * n_resolved / returned, 1) if returned else 0.0
    note = (
        "Each 24-hour line appears once; a line matching several payees on "
        "the same (date, amount) is flagged ambiguous with the first "
        "candidate in payee. Unresolved lines usually re-appear with names "
        "in the next periodic report."
    )
    if returned < total:
        note += f" Only the first {returned} of {total} lines returned; "
        note += "resolution_pct covers returned lines only."
    return {
        "committee_id": committee_id,
        "committee": _committee_display(name) or None,
        "total_lines": total,
        "returned": returned,
        "resolved": resolved,
        "unresolved": unresolved,
        "resolution_pct": pct,
        "note": note,
    }


# Curated schema gotchas surfaced by describe_table (kept short on purpose).
_TABLE_NOTES: dict[str, str] = {
    "rcpt_cd": (
        "Periodic-report contributions. Individuals: ctrib_naml=LAST, "
        "ctrib_namf=FIRST (last-first!). Organizations: ctrib_dscr. "
        "cmte_id here is the DONOR committee, not the recipient. For a "
        "complete contribution picture read receipts_all instead (adds the "
        "24-hour tables with de-dup)."
    ),
    "expn_cd": (
        "Periodic-report expenditures. payee_naml=LAST, payee_namf=FIRST "
        "for individuals; organizations in payee_naml. There is NO payee "
        "description field (only expn_dscr for the purpose). expn_date can "
        "be NULL or corrupt (some rows carry 1900/3000-era dates) — never "
        "use unbounded MAX(expn_date)."
    ),
    "s496_cd": (
        "24-hour EXPENDITURE reports (Form 496). amount, date and free-text "
        "expn_dscr ONLY — no payee name field (structural blind spot). "
        "descriptions are usually generic labels ('MAILER', 'TELEVISION "
        "ADS'), not payees. Resolve payees with the rapid_expense_vendors "
        "tool (date+amount match against the periodic re-report)."
    ),
    "s497_cd": (
        "24-hour large-CONTRIBUTION reports (Form 497). Column gotchas: "
        "amount is `amount` (NOT amt_rcvd), date is `ctrib_date`. Donor "
        "name in enty_naml (last) / enty_namf (first)."
    ),
    "s498_cd": (
        "24-hour receipt reports (Form 498). Column gotchas: amount is "
        "`amt_rcvd`, date is `date_rcvd`. Payor name in payor_naml / "
        "payor_namf."
    ),
    "receipts_all": (
        "View: normalized union of rcpt_cd + s497_cd + s498_cd with "
        "cross-source 24-hour de-duplication. Contribution tools read this, "
        "so a gift reported in both a 24-hour and a periodic report counts "
        "once. Key columns: src, receipt_date, amount, donor_naml, "
        "donor_namf, ctrib_dscr, donor_name, cmte_id (donor committee)."
    ),
    "filername_cd": (
        "Committee/filer names — INFLATED: one row per (name x contact) "
        "combo, so a filer appears ~10x. Use DISTINCT ON (filer_id) for a "
        "single name. effect_dt is flattened (not a real name-history "
        "timeline)."
    ),
    "filer_filings_cd": (
        "Maps filing_id -> filer_id. (filing_id, filer_id) PAIRS CAN BE "
        "DUPLICATED — joins that fan out on it inflate counts. Use DISTINCT "
        "or EXISTS semi-joins."
    ),
    "filer_xref_cd": (
        "Maps committee IDs (xref_id = cmte_id) -> filer_id. When a filer "
        "has several xrefs, take the latest via effect_dt DESC NULLS LAST."
    ),
    "names_cd": (
        "Name master (key: namid). Derived from the detail tables, which "
        "store name TEXT, not IDs — searching this table is not a "
        "substitute for searching rcpt_cd/expn_cd."
    ),
}


def describe_table(table_name: str) -> dict[str, Any]:
    """Return the column list, approximate row count, and known gotchas
    for a table or view.

    Use this before writing ad-hoc SQL: the 24-hour tables in particular
    have divergent column names (s497 uses `amount`/`ctrib_date`, s498
    uses `amt_rcvd`/`date_rcvd`).

    Args:
        table_name: Public schema table or view name (e.g. ``"s497_cd"``,
            ``"receipts_all"``).

    Returns:
        Dict with ``table``, ``approx_rows``, ``columns`` (name + type +
        nullable), ``notes`` (curated gotchas, may be None), and — for an
        unknown name — the list of available tables under ``available``.
    """
    t = (table_name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", t):
        return {"error": f"invalid table name: {table_name!r}"}
    rows = execute_read(
        """
        SELECT column_name,
               data_type || CASE
                   WHEN character_maximum_length IS NOT NULL
                        THEN '('::text || character_maximum_length || ')'
                   ELSE ''
               END AS data_type,
               is_nullable = 'YES' AS nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t
        ORDER BY ordinal_position
        """,
        {"t": t},
    )
    if not rows:
        available = execute_read(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        return {
            "error": f"unknown table or view: {table_name!r}",
            "available": [r["table_name"] for r in available],
        }
    rc = execute_read(
        """
        SELECT COALESCE(reltuples::bigint, 0) AS n
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = :t
        """,
        {"t": t},
    )
    return {
        "table": t,
        "approx_rows": int(rc[0]["n"]) if rc else 0,
        "columns": [
            {"name": r["column_name"], "type": r["data_type"],
             "nullable": bool(r["nullable"])}
            for r in rows
        ],
        "notes": _TABLE_NOTES.get(t),
    }


def get_server_docs() -> str:
    """Return the full server quick-start guide as markdown.

    Call this first when attaching a new agent: it covers how to use
    every tool, the data conventions, and the known caveats. (The same
    text ships in ``docs/mcp_server.md`` in the repository.)
    """
    doc = Path(__file__).resolve().parents[2] / "docs" / "mcp_server.md"
    try:
        return doc.read_text(encoding="utf-8")
    except OSError:
        return (
            "# Campaign Finance Database MCP server\n\n"
            "Read-only query tools over the CAL-ACCESS (CA SOS) campaign "
            "finance disclosure database. Full guide: docs/mcp_server.md "
            "in the campaign-finance-tracking-db repository.\n\n"
            "Tools: contributions_by_donor, top_donors_for_committee_or_candidate, "
            "committee_outlays_to, vendor_revenue, committees_paying_vendor, "
            "committee_profile, find_committees, measure_spending, "
            "donor_watch_since, upcoming_filings, filing_due_soon, "
            "payments_to_person, rapid_expense_vendors, describe_table, "
            "run_sql, get_server_docs.\n"
        )


def run_sql(sql: str, row_limit: int = 200) -> dict[str, Any]:
    """Run one read-only SQL query against the campaign-finance database.

    Escape hatch for edge-case questions no dedicated tool covers.
    Guards: one statement only, must start with SELECT/WITH/EXPLAIN;
    15 s statement timeout; capped row fetch. Runs as the read-only
    ``cfdb_reader`` role, so writes are impossible and privileged schemas
    (unredacted) stay invisible even to raw SQL — prefer a dedicated tool
    when one fits; reach for this for anything else.
    """
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        return {"error": "empty SQL"}
    first = cleaned.split(None, 1)[0].upper()
    if first not in {"SELECT", "WITH", "EXPLAIN"}:
        return {"error": f"only SELECT/WITH/EXPLAIN are allowed, got '{first}'"}
    if first == "EXPLAIN" and cleaned.upper().split(None, 2)[1:2] == ["ANALYZE"]:
        return {"error": "EXPLAIN ANALYZE executes the statement; plain EXPLAIN only"}
    if ";" in cleaned:
        return {"error": "single statement only"}
    limit = max(1, min(int(row_limit), 1000))
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = 15000"))
            conn.execute(text("SET TRANSACTION READ ONLY"))
            result = conn.execute(text(cleaned), {})
            columns = list(result.keys())
            rows = [dict(_coerce_row(dict(zip(columns, r)))) for r in result.fetchmany(limit + 1)]
        truncated = len(rows) > limit
        return {
            "columns": columns,
            "row_count": min(len(rows), limit),
            "truncated": truncated,
            "rows": rows[:limit],
        }
    except Exception as exc:  # surface SQL errors as data, never as transport errors
        return {"error": f"{type(exc).__name__}: {exc}"}
