"""Federal (FEC) independent-expenditure MCP tools.

WHY THIS MODULE EXISTS THE WAY IT DOES
------------------------------------
Every tool here reads the **de-duplicated** view ``fec.fec_ie_targeted_dedup``
and NEVER the raw ``fec.fec_ie_targeted`` table. That is deliberate and is the
single most important rule in this module.

Raw FEC Schedule E double-counts in TWO independent ways, both discovered while
reconciling the warehouse against the OpenFEC API for the CA-22 race:

  1. F24 48-hour notice vs F3X periodic report. The same expenditure is filed
     twice — once as a rapid F24 notice, again in the later F3X report — under
     the SAME ``transaction_id``. Summing the raw table roughly doubles totals.

  2. Cross-filing re-filing. A committee re-reports the same line in a later
     or amended filing under a DIFFERENT ``transaction_id``. The API's
     ``most_recent=true`` does NOT collapse this (both filings are "recent").
     We saw a single $298,835 line re-filed 8x, and ~$327M of such excess
     across the 2026 cycle. This is the same class of bug the California
     CAL-ACCESS side hit (see ``0007_transaction_dedup.sql`` and the
     ``receipts_all`` "max rows-per-source" pattern).

The view collapses both layers (see ``0017_``/``0018_`` migrations). The
practical effect on the Villegas query: raw against $4.68M -> layer-1 $3.31M
-> layer-1+2 $2.70M. A tool that summed the base table would have been off by
~73%. So the dedup is not a nicety — it is the difference between a correct
answer and a wildly inflated one.

If you add a tool here, point it at ``IE`` (the view). Do not introduce a
query against the base table; if you think you need raw rows, add them to the
view with the appropriate dedup instead.

Scope: candidate-targeted independent expenditures (Schedule E) from the
OpenFEC API. Cycle-agnostic: pass ``cycle`` (the FEC two-year period, stored in
``election_year``) to scope to one cycle, or omit it to span every loaded cycle.

Tools:
  1. fec_find_candidate      — name/state/district -> candidate_id(s)
  2. fec_ie_summary          — for/against totals for a candidate (date-range ok)
  3. fec_top_spending        — top N spenders for or against a candidate
  4. fec_spender_breakdown   — what a given spender bought (payee/purpose/candidate)
  5. fec_race_overview       — all IEs in a state/district race by candidate & side
"""

from __future__ import annotations

import logging
from typing import Any

from core.mcp.db import execute_read
from core.mcp.tools import _coerce_row, _money

logger = logging.getLogger(__name__)

# The de-duplicated Schedule E view. ALWAYS query this, never the base table.
# It collapses (L1) the F24-notice / F3X-report duplicate and (L2) the
# cross-filing re-filing duplicate, so every SUM below is dedup-safe by
# construction. See the module docstring for why this matters (~73% error if
# you sum the raw table instead).
IE = "fec.fec_ie_targeted_dedup"

# The de-duplicated view PLUS candidate-name resolution (migration 0019).
# resolved_candidate_id folds NULL-id records into their named candidate via an
# EXACT canonical-name match (single-candidate guard), and merges name-order
# variants. Use this for any PER-CANDIDATE total — grouping by the raw
# candidate_id drops the NULL-id records and splits name variants.
RESOLVED = "fec.fec_ie_targeted_resolved"

# FEC election phases. The stored election_type is a phase LETTER + year
# (e.g. 'P2026', 'G2026', 'S2025'); some rows are a bare letter. We match on
# the leading letter so a friendly name works with or without the year suffix.
#   P primary  G general  R runoff  S special  C convention  O other
_PHASE_MAP = {
    "primary": "P",
    "general": "G",
    "runoff": "R",
    "special": "S",
    "convention": "C",
    "other": "O",
}
_VALID_PHASES = set(_PHASE_MAP.values())


def _election_type_clause(
    election_type: str | None, col: str = "election_type"
) -> tuple[str, dict[str, Any]]:
    """Filter by election phase (primary / general / special / runoff / ...).

    WHY: `cycle` alone is only the two-year PERIOD — it does not tell you WHICH
    election within it. A cycle can contain a special, a primary, a runoff and a
    general. Callers asking about "the primary" or "the general" need this.
    Accepts a friendly name or a raw FEC phase letter; matches the leading
    letter of the stored `election_type`.
    """
    if not election_type:
        return "", {}
    et = str(election_type).strip()
    letter = _PHASE_MAP.get(et.lower()) or (et[:1].upper() if et else "")
    if letter not in _VALID_PHASES:
        raise ValueError(
            f"unknown election_type {election_type!r}; use primary/general/"
            f"special/runoff/convention/other or a P/G/R/S/C/O code"
        )
    return f" AND left({col}, 1) = :ephase", {"ephase": letter}


def _cycle_clause(col: str, cycle: int | None) -> tuple[str, dict[str, Any]]:
    if cycle is None:
        return "", {}
    return f" AND {col} = :cycle", {"cycle": cycle}


def _date_clause(col: str, start: str | None, end: str | None) -> tuple[str, dict[str, Any]]:
    clause, params = "", {}
    if start:
        clause += f" AND {col} >= :start"
        params["start"] = start
    if end:
        clause += f" AND {col} < :end"  # exclusive upper bound
        params["end"] = end
    return clause, params


# --------------------------------------------------------------------------- #
#  1. fec_find_candidate
# --------------------------------------------------------------------------- #
def fec_find_candidate(
    name: str,
    state: str | None = None,
    district: str | None = None,
    cycle: int | None = None,
    limit: int = 20,
    min_similarity: float = 0.30,
) -> list[dict[str, Any]]:
    """Resolve a candidate name to FEC candidate id(s) from Schedule E records.

    Use the returned ``candidate_id`` in the other ``fec_*`` tools. Matching is
    a case-insensitive substring on the candidate name (stored ``LAST, FIRST``)
    OR a fuzzy trigram match (pg_trgm ``similarity``), so typos and reordered
    names still surface. Each hit carries a ``match`` score (0..1): substring
    hits report 1.0, fuzzy hits report the trigram similarity.

    WHY fuzzy lives HERE and not in the aggregation: a wrong fuzzy merge in a
    SUM silently corrupts a total, but here a human/agent reviews the ranked
    candidates and their scores before choosing an id. The per-candidate totals
    use EXACT canonical-name resolution instead (see migration 0019).

    Args:
        name: Candidate name fragment (e.g. ``"Villegas"``).
        state: Optional 2-letter office state (e.g. ``"CA"``).
        district: Optional office district (e.g. ``"22"``; ``"00"`` for
            statewide/senate).
        cycle: Optional FEC cycle year (e.g. ``2026``).
        limit: Max candidates to return (default 20).
        min_similarity: Minimum trigram similarity for a fuzzy (non-substring)
            hit (default 0.30). Raise it to be stricter.

    Returns:
        List of ``{candidate_id, candidate_name, office, office_state,
        office_district, party, ie_records, match}`` dicts, best match first.
    """
    cyc, cyc_params = _cycle_clause("election_year", cycle)
    # resolved_candidate_id so the id we hand back is the one the other tools
    # aggregate on (NULL-id records folded in). similarity() gives the fuzzy
    # score; ILIKE gives the exact-substring path.
    sql = f"""
        SELECT resolved_candidate_id AS candidate_id,
               MAX(resolved_candidate_name) AS candidate_name,
               MAX(office) AS office,
               MAX(office_state) AS office_state,
               MAX(office_district) AS office_district,
               MAX(party_affiliation) AS party,
               COUNT(*) AS ie_records,
               MAX(similarity(resolved_candidate_name, :q)) AS match
        FROM {RESOLVED}
        WHERE resolved_candidate_id IS NOT NULL
          AND (resolved_candidate_name ILIKE :name
               OR similarity(resolved_candidate_name, :q) >= :min_sim)
          {'AND office_state = :state' if state else ''}
          {'AND office_district = :district' if district else ''}
          {cyc}
        GROUP BY resolved_candidate_id
        ORDER BY match DESC, candidate_name
        LIMIT :lim
    """
    params: dict[str, Any] = {
        "name": f"%{name}%",
        "q": name,
        "min_sim": min_similarity,
        "lim": limit,
    }
    if state:
        params["state"] = state
    if district:
        params["district"] = district
    params.update(cyc_params)
    rows = execute_read(sql, params)
    return [_coerce_row(r) for r in rows]


# --------------------------------------------------------------------------- #
#  2. fec_ie_summary
# --------------------------------------------------------------------------- #
def fec_ie_summary(
    candidate_id: str,
    cycle: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    election_type: str | None = None,
) -> dict[str, Any]:
    """De-duplicated for/against independent-expenditure totals for a candidate.

    Answers "how much was spent for and against candidate X" without the
    F24-notice / F3X-report double-count. Optionally narrow to a date window
    (``start_date`` inclusive, ``end_date`` exclusive, ISO ``YYYY-MM-DD``) or a
    specific election phase.

    Args:
        candidate_id: FEC candidate id (e.g. ``"H6CA22190"``).
        cycle: Optional FEC cycle year (e.g. ``2026``).
        start_date: Optional ISO start date (inclusive).
        end_date: Optional ISO end date (exclusive).
        election_type: Optional election phase — ``primary``/``general``/
            ``special``/``runoff``/``convention``/``other`` (or a P/G/R/S/C/O
            code). ``cycle`` alone is only the two-year period; this picks WHICH
            election within it.

    Returns:
        ``{candidate_id, for_total, against_total, for_count, against_count,
        net, cycle, start_date, end_date, election_type}``.
    """
    cyc, cyc_params = _cycle_clause("election_year", cycle)
    dt, dt_params = _date_clause("expenditure_date", start_date, end_date)
    et, et_params = _election_type_clause(election_type)
    # The SUMs below are dedup-safe ONLY because {RESOLVED} is the de-duplicated
    # view. The FILTER clauses split S(for)/O(against) without a second pass.
    # We match on resolved_candidate_id (not the raw candidate_id) so the
    # NULL-id records folded into this candidate by name are INCLUDED.
    # Do NOT repoint this at fec.fec_ie_targeted — that re-introduces the
    # F24/F3X and cross-filing double-count (see module docstring).
    sql = f"""
        SELECT
            COALESCE(SUM(expenditure_amount) FILTER (WHERE support_oppose = 'S'), 0) AS for_total,
            COALESCE(SUM(expenditure_amount) FILTER (WHERE support_oppose = 'O'), 0) AS against_total,
            COUNT(*) FILTER (WHERE support_oppose = 'S') AS for_count,
            COUNT(*) FILTER (WHERE support_oppose = 'O') AS against_count
        FROM {RESOLVED}
        WHERE resolved_candidate_id = :candidate
          {cyc}
          {dt}
          {et}
    """
    params: dict[str, Any] = {"candidate": candidate_id}
    params.update(cyc_params)
    params.update(dt_params)
    params.update(et_params)
    row = execute_read(sql, params)[0]
    for_total = _money(row.get("for_total"))
    against_total = _money(row.get("against_total"))
    return {
        "candidate_id": candidate_id,
        "for_total": for_total,
        "against_total": against_total,
        "for_count": int(row.get("for_count") or 0),
        "against_count": int(row.get("against_count") or 0),
        "net": round(for_total - against_total, 2),
        "cycle": cycle,
        "start_date": start_date,
        "end_date": end_date,
        "election_type": election_type,
    }


# --------------------------------------------------------------------------- #
#  3. fec_top_spending
# --------------------------------------------------------------------------- #
def fec_top_spending(
    candidate_id: str,
    side: str = "against",
    cycle: int | None = None,
    limit: int = 5,
    election_type: str | None = None,
) -> list[dict[str, Any]]:
    """Top N committees spending for or against a candidate (de-duplicated).

    Args:
        candidate_id: FEC candidate id (e.g. ``"H6CA22190"``).
        side: ``"against"`` (opposing IEs) or ``"for"`` (supporting IEs).
        cycle: Optional FEC cycle year.
        limit: Max spenders to return (default 5).
        election_type: Optional election phase (primary/general/special/...).

    Returns:
        List of ``{spender_id, spender_name, ies, total}`` sorted by total desc.
    """
    side_norm = (side or "against").strip().lower()
    if side_norm not in ("against", "for"):
        raise ValueError("side must be 'against' or 'for'")
    op = "O" if side_norm == "against" else "S"
    cyc, cyc_params = _cycle_clause("t.election_year", cycle)
    et, et_params = _election_type_clause(election_type, "t.election_type")
    # SUM over the de-duplicated, name-resolved view: each unique expenditure
    # counts once, and NULL-id records folded into this candidate are included.
    # committee_name is LEFT-JOINed (the IE table stores only the spender id).
    sql = f"""
        SELECT t.spender_id,
               COALESCE(c.committee_name, t.spender_id) AS spender_name,
               COUNT(*) AS ies,
               SUM(t.expenditure_amount) AS total
        FROM {RESOLVED} t
        LEFT JOIN fec.fec_committees c ON c.committee_id = t.spender_id
        WHERE t.resolved_candidate_id = :candidate
          AND t.support_oppose = :op
          {cyc}
          {et}
        GROUP BY t.spender_id, c.committee_name
        ORDER BY total DESC
        LIMIT :lim
    """
    params: dict[str, Any] = {"candidate": candidate_id, "op": op, "lim": limit}
    params.update(cyc_params)
    params.update(et_params)
    rows = execute_read(sql, params)
    out: list[dict[str, Any]] = []
    for r in rows:
        c = _coerce_row(r)
        out.append({
            "spender_id": c.get("spender_id"),
            "spender_name": c.get("spender_name"),
            "ies": int(c.get("ies") or 0),
            "total": _money(c.get("total")),
        })
    return out


# --------------------------------------------------------------------------- #
#  4. fec_spender_breakdown
# --------------------------------------------------------------------------- #
def fec_spender_breakdown(
    spender_id: str,
    candidate_id: str | None = None,
    cycle: int | None = None,
    limit: int = 50,
    election_type: str | None = None,
) -> dict[str, Any]:
    """What a given spender bought, de-duplicated, by payee / purpose / target.

    Answers "what did Committee X spend on" — the payees, the purposes
    (ad buy vs production vs other), and which candidates the money targeted.

    Args:
        spender_id: Spending committee id (e.g. ``"C00859660"``).
        candidate_id: Optional target candidate to scope the breakdown.
        cycle: Optional FEC cycle year.
        limit: Max rows per sub-breakdown (default 50).
        election_type: Optional election phase (primary/general/special/...).

    Returns:
        ``{spender_id, spender_name, total, by_payee: [...], by_purpose: [...],
        by_target: [...]}``.
    """
    cyc, cyc_params = _cycle_clause("election_year", cycle)
    et, et_params = _election_type_clause(election_type)
    cand = " AND resolved_candidate_id = :candidate" if candidate_id else ""
    base_params: dict[str, Any] = {"spender": spender_id}
    if candidate_id:
        base_params["candidate"] = candidate_id
    base_params.update(cyc_params)
    base_params.update(et_params)

    where = f"WHERE spender_id = :spender {cand} {cyc} {et}"

    # All four sub-queries below run against the de-duplicated, name-resolved
    # view, so the by-payee / by-purpose / by-target totals and the grand total
    # all agree (no F24/F3X or cross-filing inflation). The breakdown is
    # intentionally split so "ad buy" vs "ad production" purposes don't blur.
    total_row = execute_read(
        f"SELECT COALESCE(SUM(expenditure_amount),0) AS total, "
        f"COUNT(*) AS n FROM {RESOLVED} {where}",
        base_params,
    )[0]

    name_rows = execute_read(
        f"SELECT MAX(c.committee_name) AS nm FROM {RESOLVED} t "
        f"LEFT JOIN fec.fec_committees c ON c.committee_id = t.spender_id {where}",
        base_params,
    )[0]

    by_payee = execute_read(
        f"SELECT payee, COUNT(*) AS n, SUM(expenditure_amount) AS total "
        f"FROM {RESOLVED} {where} GROUP BY payee ORDER BY total DESC LIMIT :lim",
        {**base_params, "lim": limit},
    )
    by_purpose = execute_read(
        f"SELECT purpose, COUNT(*) AS n, SUM(expenditure_amount) AS total "
        f"FROM {RESOLVED} {where} GROUP BY purpose ORDER BY total DESC LIMIT :lim",
        {**base_params, "lim": limit},
    )
    by_target = execute_read(
        f"SELECT resolved_candidate_id AS candidate_id, "
        f"MAX(resolved_candidate_name) AS candidate_name, "
        f"CASE support_oppose WHEN 'S' THEN 'for' WHEN 'O' THEN 'against' "
        f"ELSE 'unknown' END AS side, COUNT(*) AS n, "
        f"SUM(expenditure_amount) AS total "
        f"FROM {RESOLVED} {where} GROUP BY resolved_candidate_id, side "
        f"ORDER BY total DESC LIMIT :lim",
        {**base_params, "lim": limit},
    )

    def _slim(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            c = _coerce_row(r)
            d = {k: c.get(k) for k in keys if k not in ("n", "total")}
            d["count"] = int(c.get("n") or 0)
            d["total"] = _money(c.get("total"))
            out.append(d)
        return out

    return {
        "spender_id": spender_id,
        "spender_name": (name_rows.get("nm") or spender_id),
        "total": _money(total_row.get("total")),
        "ie_count": int(total_row.get("n") or 0),
        "by_payee": _slim(by_payee, ["payee"]),
        "by_purpose": _slim(by_purpose, ["purpose"]),
        "by_target": _slim(by_target, ["candidate_id", "candidate_name", "side"]),
    }


# --------------------------------------------------------------------------- #
#  5. fec_race_overview
# --------------------------------------------------------------------------- #
def fec_race_overview(
    state: str,
    district: str,
    cycle: int | None = None,
    limit: int = 50,
    election_type: str | None = None,
) -> dict[str, Any]:
    """All independent expenditures in a state/district race, by candidate & side.

    Gives the full for/against picture across every candidate targeted in one
    race (e.g. all of CA-22), de-duplicated.

    Args:
        state: 2-letter office state (e.g. ``"CA"``).
        district: Office district (e.g. ``"22"``; ``"00"`` for statewide).
        cycle: Optional FEC cycle year.
        limit: Max candidate rows (default 50).
        election_type: Optional election phase (primary/general/special/...).
            ``cycle`` alone spans the whole two-year period; this isolates one
            election (e.g. ``"primary"`` for the P rows).

    Returns:
        ``{state, district, cycle, election_type, total_for, total_against,
        by_candidate: [{candidate_id, candidate_name, for_total, against_total,
        for_count, against_count}]}``.
    """
    cyc, cyc_params = _cycle_clause("election_year", cycle)
    et, et_params = _election_type_clause(election_type)
    # De-duplicated + name-resolved view: the per-candidate for/against sums
    # and the race totals are free of the F24/F3X + cross-filing double-count,
    # AND NULL-id records are folded into their named candidate (grouping by the
    # raw candidate_id would drop them). Ordered by combined (for+against) so
    # the most heavily-battled candidates surface first.
    sql = f"""
        SELECT resolved_candidate_id AS candidate_id,
               MAX(resolved_candidate_name) AS candidate_name,
               COALESCE(SUM(expenditure_amount) FILTER (WHERE support_oppose='S'), 0) AS for_total,
               COALESCE(SUM(expenditure_amount) FILTER (WHERE support_oppose='O'), 0) AS against_total,
               COUNT(*) FILTER (WHERE support_oppose='S') AS for_count,
               COUNT(*) FILTER (WHERE support_oppose='O') AS against_count
        FROM {RESOLVED}
        WHERE office_state = :state
          AND office_district = :district
          AND resolved_candidate_id IS NOT NULL
          {cyc}
          {et}
        GROUP BY resolved_candidate_id
        ORDER BY (COALESCE(SUM(expenditure_amount) FILTER (WHERE support_oppose='S'),0)
               + COALESCE(SUM(expenditure_amount) FILTER (WHERE support_oppose='O'),0)) DESC
        LIMIT :lim
    """
    params: dict[str, Any] = {"state": state, "district": district, "lim": limit}
    params.update(cyc_params)
    params.update(et_params)
    rows = execute_read(sql, params)
    by_candidate = []
    tot_for = tot_against = 0.0
    for r in rows:
        c = _coerce_row(r)
        f = _money(c.get("for_total"))
        a = _money(c.get("against_total"))
        tot_for += f
        tot_against += a
        by_candidate.append({
            "candidate_id": c.get("candidate_id"),
            "candidate_name": c.get("candidate_name"),
            "for_total": f,
            "against_total": a,
            "for_count": int(c.get("for_count") or 0),
            "against_count": int(c.get("against_count") or 0),
        })
    return {
        "state": state,
        "district": district,
        "cycle": cycle,
        "election_type": election_type,
        "total_for": round(tot_for, 2),
        "total_against": round(tot_against, 2),
        "by_candidate": by_candidate,
    }


# --------------------------------------------------------------------------- #
#  6. fec_ie_by_org
# --------------------------------------------------------------------------- #
def fec_ie_by_org(
    state: str,
    district: str,
    cycle: int | None = None,
    election_type: str | None = None,
    min_amount: float = 100000.0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Every (org -> candidate -> side) IE combination above a threshold.

    WHY: a flat "total by org" hides that one committee can spend BOTH for and
    against, and across MULTIPLE candidates in the same race. This returns one
    row per (org, candidate, side) so, e.g., "Serving CA spent $X against
    Candidate A" and "Serving CA spent $Y for Candidate B" are SEPARATE rows.
    Amounts are de-duplicated (F24/F3X + cross-filing) and candidates are
    name-resolved (NULL-id folded), so each row is the real money.

    Args:
        state: 2-letter office state (e.g. ``"CA"``).
        district: Office district (e.g. ``"48"``; ``"00"`` for statewide).
        cycle: Optional FEC cycle year (e.g. ``2026``).
        election_type: Optional election phase (primary/general/special/...).
        min_amount: Only combinations whose total EXCEEDS this (default 100000).
        limit: Max rows (default 200).

    Returns:
        List of ``{spender_id, org, candidate_id, candidate, side, purpose,
        amount, ie_count}`` sorted by amount desc. ``purpose`` is a human
        string: "in support of CANDIDATE" / "in opposition to CANDIDATE".
    """
    cyc, cyc_params = _cycle_clause("t.election_year", cycle)
    et, et_params = _election_type_clause(election_type, "t.election_type")
    # One row per (org, candidate, side). HAVING filters the GROUP total, so a
    # big org's for-side and against-side are evaluated (and reported) apart.
    sql = f"""
        SELECT t.spender_id,
               COALESCE(c.committee_name, max(t.committee_name), t.spender_id) AS org,
               t.resolved_candidate_id AS candidate_id,
               max(t.resolved_candidate_name) AS candidate,
               CASE t.support_oppose WHEN 'S' THEN 'for' WHEN 'O' THEN 'against'
                    ELSE 'unknown' END AS side,
               sum(t.expenditure_amount) AS amount,
               count(*) AS ie_count
        FROM {RESOLVED} t
        LEFT JOIN fec.fec_committees c ON c.committee_id = t.spender_id
        WHERE t.office_state = :state
          AND t.office_district = :district
          AND t.resolved_candidate_id IS NOT NULL
          {cyc}
          {et}
        GROUP BY t.spender_id, c.committee_name, t.resolved_candidate_id, t.support_oppose
        HAVING sum(t.expenditure_amount) > :min_amount
        ORDER BY amount DESC
        LIMIT :lim
    """
    params: dict[str, Any] = {
        "state": state,
        "district": district,
        "min_amount": min_amount,
        "lim": limit,
    }
    params.update(cyc_params)
    params.update(et_params)
    rows = execute_read(sql, params)
    out: list[dict[str, Any]] = []
    for r in rows:
        c = _coerce_row(r)
        cand = c.get("candidate") or c.get("candidate_id")
        side = c.get("side")
        purpose = (
            f"in support of {cand}" if side == "for"
            else f"in opposition to {cand}" if side == "against"
            else f"unknown regarding {cand}"
        )
        out.append({
            "spender_id": c.get("spender_id"),
            "org": c.get("org"),
            "candidate_id": c.get("candidate_id"),
            "candidate": cand,
            "side": side,
            "purpose": purpose,
            "amount": _money(c.get("amount")),
            "ie_count": int(c.get("ie_count") or 0),
        })
    return out
