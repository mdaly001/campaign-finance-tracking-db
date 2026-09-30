"""Cross-source (state + federal) entity money tools.

WHY THIS MODULE EXISTS
--------------------
A committee/PAC can be registered in BOTH the California (CAL-ACCESS) state
system and the federal (FEC) system, and its money is split across them. The
per-source tools (core.mcp.tools = state, core.mcp.federal_tools = federal)
each see only one side. These two tools answer, for a single named entity:

  * entity_contributions  — who gave TO the entity (money IN), both sources.
  * entity_expenditures   — what the entity spent (money OUT), both sources.

Each takes an optional `year` (calendar year of the transaction, or None for
all) and an optional `min_amount` (only counter-parties whose AGGREGATED total
for the entity meets the threshold — filters small-dollar noise). Results are
tagged by `source` so you can see which side of the ledger each record came
from, and the entity is resolved by NAME across both registries (a PAC may
exist in one, the other, or both).

IMPORTANT — totals vs. the display list:
`total`, `by_source`, `by_year`, and `record_count` are the FULL totals over
every matching record (independent of `limit`/`min_amount`). The
`contributors` / `expenditures` lists are the top-N (threshold-filtered) view
for display. So the list may sum to less than `total` when there are more
counter-parties than `limit` — that is expected; the authoritative money
figures are the full totals.

DEDUP / SOURCE NOTES
------------------
* Federal contributions: fec_individual_contributions + fec_other_contributions
  (recipient `committee_id`; donor `contributor_name`).
* State contributions: receipts_all (recipient `cmte_id`, donor `donor_name`).
* Federal expenditures: the de-duplicated targeted view fec.fec_ie_targeted_resolved
  (2026) PLUS the 2024 full load fec.fec_independent_expenditures collapsed
  inline with DISTINCT ON (committee_id, transaction_id) — that table re-reports
  the same IE across every periodic report (Q1/Q2/Q3/YE), so raw rows
  triple-count. NOTE: the 2024 bulk load did NOT populate candidate_name /
  supported_opposed, so those rows surface as "(2024 load: candidate not
  captured)" — the amount is real, the targeting is not in that load.
* State expenditures: expn_all (payee `payee_naml`, `purpose`).
"""

from __future__ import annotations

import logging
from typing import Any

from core.mcp.db import execute_read
from core.mcp.tools import _coerce_row, _money

logger = logging.getLogger(__name__)


def _resolve_entity(name: str) -> dict[str, list[dict[str, Any]]]:
    """Find federal committee_ids and state filer_ids matching an entity name."""
    fed = execute_read(
        "SELECT committee_id AS id, committee_name AS name, state "
        "FROM fec.fec_committees WHERE committee_name ILIKE :p "
        "ORDER BY committee_name",
        {"p": f"%{name}%"},
    )
    st = execute_read(
        "SELECT DISTINCT filer_id::text AS id, naml AS name "
        "FROM filername_cd "
        "WHERE filer_id IS NOT NULL AND (naml ILIKE :p OR coalesce(namt,'') ILIKE :p) "
        "ORDER BY naml",
        {"p": f"%{name}%"},
    )
    return {
        "federal": [_coerce_row(r) for r in fed],
        "state": [_coerce_row(r) for r in st],
    }


def _year_clause(col: str, year: int | None) -> tuple[str, dict[str, Any]]:
    if year is None:
        return "", {}
    return f" AND extract(year from {col})::int = :year", {"year": year}


def _merge(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0.0) + v
    return out


# FPPC Schedule E returned-contribution descriptions are free-text, but every
# genuine one pairs "contribution" with a refund/return verb. Verified against
# the live data: all ~40 distinct purposes matching BOTH terms are real refunds
# (Refund of Contribution, Contribution Return, Returned Contribution, Partial
# Refund of General Election Contribution, ...) with no false positives.
RETURNED_CONTRIB_PURPOSE = "purpose ~* 'contrib' AND purpose ~* 'refund|return'"


def _year_totals(
    table: str, date_col: str, amount_col: str, id_col: str,
    ids: list[str], year: int | None,
) -> dict[str, float]:
    """Full money per calendar year for these ids (no counterparty grouping)."""
    yc, yp = _year_clause(date_col, year)
    rows = execute_read(
        f"SELECT extract(year from {date_col})::int AS yr, sum({amount_col}) AS amt "
        f"FROM {table} WHERE {id_col} = ANY(:ids) {yc} GROUP BY 1",
        {"ids": ids, **yp},
    )
    out: dict[str, float] = {}
    for r in rows:
        if r.get("yr") is not None:
            k = str(int(r["yr"]))
            out[k] = out.get(k, 0.0) + _money(r.get("amt"))
    return out


def _grand(
    table: str, amount_col: str, id_col: str, ids: list[str], year: int | None,
    date_col: str,
) -> tuple[float, int]:
    """Full (amount, record_count) for these ids in one table."""
    yc, yp = _year_clause(date_col, year)
    row = execute_read(
        f"SELECT coalesce(sum({amount_col}),0) AS amt, count(*) AS n "
        f"FROM {table} WHERE {id_col} = ANY(:ids) {yc}",
        {"ids": ids, **yp},
    )[0]
    return _money(row.get("amt")), int(row.get("n") or 0)


def entity_contributions(
    entity: str,
    year: int | None = None,
    min_amount: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Who gave TO an entity (money IN), across BOTH state and federal filings.

    Args:
        entity: Entity name to resolve (e.g. ``"Upset The Setup"``). Matched
            against federal committee names and state filer names.
        year: Optional calendar year of the contribution (None = all years).
        min_amount: Only include contributors whose aggregated total to this
            entity is >= this (None = no threshold).
        limit: Max contributor rows to return (default 200).

    Returns:
        ``{entity, matched: {federal:[...], state:[...]}, total, record_count,
        by_source: {federal, state}, by_year: {...}, contributors:
        [{source, name, amount, count}]}`` sorted by amount desc.
    """
    matched = _resolve_entity(entity)
    fed_ids = [m["id"] for m in matched["federal"]]
    st_ids = [m["id"] for m in matched["state"]]

    by_source = {"federal": 0.0, "state": 0.0}
    by_year: dict[str, float] = {}
    record_count = 0
    contributors: list[dict[str, Any]] = []
    having = " HAVING sum(amt) >= :min_amount" if min_amount is not None else ""

    if fed_ids:
        # Full federal totals (individual + other), independent of the list.
        for tbl in ("fec_individual_contributions", "fec_other_contributions"):
            amt, n = _grand(f"fec.{tbl}", "contribution_amount", "committee_id",
                           fed_ids, year, "contribution_date")
            by_source["federal"] += amt
            record_count += n
            by_year = _merge(by_year, _year_totals(
                f"fec.{tbl}", "contribution_date", "contribution_amount",
                "committee_id", fed_ids, year))
        # Display list: aggregated by donor, threshold-filtered, top-N.
        yc, yp = _year_clause("contribution_date", year)
        hparams = {"ids": fed_ids, **yp}
        if min_amount is not None:
            hparams["min_amount"] = min_amount
        rows = execute_read(
            f"SELECT nm AS name, sum(amt) AS amount, count(*) AS n FROM ("
            f"  SELECT coalesce(nullif(contributor_name,''), other_committee_id, 'UNKNOWN') "
            f"  AS nm, contribution_amount AS amt FROM ("
            f"    SELECT contributor_name, other_committee_id, contribution_amount "
            f"    FROM fec.fec_individual_contributions WHERE committee_id = ANY(:ids) {yc}"
            f"    UNION ALL"
            f"    SELECT contributor_name, other_committee_id, contribution_amount "
            f"    FROM fec.fec_other_contributions WHERE committee_id = ANY(:ids) {yc}"
            f"  ) u"
            f") d GROUP BY 1{having} ORDER BY amount DESC LIMIT :lim",
            {**hparams, "lim": limit},
        )
        for r in rows:
            c = _coerce_row(r)
            contributors.append({
                "source": "federal", "name": c.get("name"),
                "amount": _money(c.get("amount")), "count": int(c.get("n") or 0),
            })

    if st_ids:
        amt, n = _grand("receipts_all", "amount", "cmte_id", st_ids, year,
                        "receipt_date")
        by_source["state"] += amt
        record_count += n
        by_year = _merge(by_year, _year_totals(
            "receipts_all", "receipt_date", "amount", "cmte_id", st_ids, year))
        yc, yp = _year_clause("receipt_date", year)
        hparams = {"ids": st_ids, **yp}
        if min_amount is not None:
            hparams["min_amount"] = min_amount
        rows = execute_read(
            f"SELECT nm AS name, sum(amt) AS amount, count(*) AS n FROM ("
            f"  SELECT coalesce(nullif(donor_name,''), donor_naml, 'UNKNOWN') AS nm, "
            f"  amount AS amt FROM receipts_all WHERE cmte_id = ANY(:ids) {yc}"
            f") d GROUP BY 1{having} ORDER BY amount DESC LIMIT :lim",
            {**hparams, "lim": limit},
        )
        for r in rows:
            c = _coerce_row(r)
            contributors.append({
                "source": "state", "name": c.get("name"),
                "amount": _money(c.get("amount")), "count": int(c.get("n") or 0),
            })

    contributors.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "entity": entity,
        "matched": matched,
        "total": round(by_source["federal"] + by_source["state"], 2),
        "record_count": record_count,
        "by_source": {k: round(v, 2) for k, v in by_source.items()},
        "by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "contributors": contributors[:limit],
    }


def entity_expenditures(
    entity: str,
    year: int | None = None,
    min_amount: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """What an entity spent (money OUT), across BOTH state and federal filings.

    Args:
        entity: Entity name to resolve (federal committee / state filer).
        year: Optional calendar year of the expenditure (None = all years).
        min_amount: Only include targets whose aggregated total from this
            entity is >= this (None = no threshold).
        limit: Max expenditure rows to return (default 200).

    Returns:
        ``{entity, matched, total, record_count, by_source, by_year,
        expenditures: [{source, target, side, purpose, amount, count}]}``.
    """
    matched = _resolve_entity(entity)
    fed_ids = [m["id"] for m in matched["federal"]]
    st_ids = [m["id"] for m in matched["state"]]

    by_source = {"federal": 0.0, "state": 0.0}
    by_year: dict[str, float] = {}
    record_count = 0
    expenditures: list[dict[str, Any]] = []
    having = " HAVING sum(amount) >= :min_amount" if min_amount is not None else ""

    if fed_ids:
        # Full federal totals: 2026 deduped view + 2024 deduped-by-txn load.
        amt, n = _grand("fec.fec_ie_targeted_resolved", "expenditure_amount",
                        "spender_id", fed_ids, year, "expenditure_date")
        by_source["federal"] += amt
        record_count += n
        by_year = _merge(by_year, _year_totals(
            "fec.fec_ie_targeted_resolved", "expenditure_date",
            "expenditure_amount", "spender_id", fed_ids, year))
        # 2024 full total is over DISTINCT txn (deduped), so compute via the
        # deduped subquery rather than the raw grand (which triple-counts).
        yc, yp = _year_clause("expenditure_date", year)
        dedup24 = (
            f"SELECT target, side, amount FROM ("
            f"  SELECT DISTINCT ON (committee_id, transaction_id) "
            f"  coalesce(nullif(candidate_name,''),"
            f"'(2024 load: candidate not captured)') AS target, "
            f"  CASE supported_opposed WHEN 'S' THEN 'for' WHEN 'O' THEN 'against' "
            f"  ELSE 'unknown' END AS side, expenditure_amount AS amount "
            f"  FROM fec.fec_independent_expenditures "
            f"  WHERE committee_id = ANY(:ids) {yc} "
            f"  ORDER BY committee_id, transaction_id, expenditure_amount DESC"
            f") dd"
        )
        d24 = execute_read(
            f"SELECT coalesce(sum(amount),0) AS amt, count(*) AS n FROM ({dedup24}) x",
            {"ids": fed_ids, **yp})[0]
        by_source["federal"] += _money(d24.get("amt"))
        record_count += int(d24.get("n") or 0)
        # by_year for 2024 (deduped)
        d24y = execute_read(
            f"SELECT extract(year from ed)::int AS yr, sum(amount) AS amt FROM ("
            f"  SELECT DISTINCT ON (committee_id, transaction_id) "
            f"  expenditure_date AS ed, expenditure_amount AS amount "
            f"  FROM fec.fec_independent_expenditures "
            f"  WHERE committee_id = ANY(:ids) {yc} "
            f"  ORDER BY committee_id, transaction_id, expenditure_amount DESC"
            f") x GROUP BY 1",
            {"ids": fed_ids, **yp})
        for r in d24y:
            if r.get("yr") is not None:
                k = str(int(r["yr"]))
                by_year[k] = by_year.get(k, 0.0) + _money(r.get("amt"))

        # Display list: 2026 targeted (candidate+side) + 2024, top-N.
        hparams = {"ids": fed_ids, **yp}
        if min_amount is not None:
            hparams["min_amount"] = min_amount
        rows = execute_read(
            f"SELECT target, side, sum(amount) AS amount, count(*) AS n FROM ("
            f"  SELECT resolved_candidate_name AS target, "
            f"  CASE support_oppose WHEN 'S' THEN 'for' WHEN 'O' THEN 'against' "
            f"  ELSE 'unknown' END AS side, expenditure_amount AS amount "
            f"  FROM fec.fec_ie_targeted_resolved WHERE spender_id = ANY(:ids) {yc}"
            f"  UNION ALL {dedup24}"
            f") d GROUP BY 1, 2{having} ORDER BY amount DESC LIMIT :lim",
            {**hparams, "lim": limit},
        )
        for r in rows:
            c = _coerce_row(r)
            expenditures.append({
                "source": "federal", "target": c.get("target"),
                "side": c.get("side"), "purpose": None,
                "amount": _money(c.get("amount")), "count": int(c.get("n") or 0),
            })

    if st_ids:
        amt, n = _grand("expn_all", "amount", "cmte_id", st_ids, year,
                        "expense_date")
        by_source["state"] += amt
        record_count += n
        by_year = _merge(by_year, _year_totals(
            "expn_all", "expense_date", "amount", "cmte_id", st_ids, year))
        yc, yp = _year_clause("expense_date", year)
        hparams = {"ids": st_ids, **yp}
        if min_amount is not None:
            hparams["min_amount"] = min_amount
        rows = execute_read(
            f"SELECT target, max(purpose) AS purpose, sum(amount) AS amount, count(*) AS n FROM ("
            f"  SELECT coalesce(nullif(payee_naml,''),'(unknown payee)') AS target, "
            f"  purpose, amount FROM expn_all WHERE cmte_id = ANY(:ids) {yc}"
            f") d GROUP BY 1{having} ORDER BY amount DESC LIMIT :lim",
            {**hparams, "lim": limit},
        )
        for r in rows:
            c = _coerce_row(r)
            expenditures.append({
                "source": "state", "target": c.get("target"),
                "side": None, "purpose": c.get("purpose"),
                "amount": _money(c.get("amount")), "count": int(c.get("n") or 0),
            })

    expenditures.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "entity": entity,
        "matched": matched,
        "total": round(by_source["federal"] + by_source["state"], 2),
        "record_count": record_count,
        "by_source": {k: round(v, 2) for k, v in by_source.items()},
        "by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "expenditures": expenditures[:limit],
    }


def returned_contributions(
    entity: str,
    year: int | None = None,
    min_amount: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Contributions an entity RETURNED / REFUNDED to donors, both sources.

    WHY THIS TOOL EXISTS
    ------------------
    When a committee can't keep a contribution (over limit, ineligible, or a
    duplicate) it sends the money back. That refund is NOT visible on the
    contribution side — it is booked as an outgoing record, so the standard
    contributions view overstates what a committee actually kept. Surfacing
    refunds separately lets you reconcile "received" vs "kept" and spot
    committees that return a lot of money.

    How each source encodes a refund:
      * State (CAL-ACCESS): a Schedule E expenditure (expn_all) whose purpose
        is a contribution refund/return (see RETURNED_CONTRIB_PURPOSE). The
        donor who got the money back is the payee.
      * Federal (FEC): a NEGATIVE contribution (a refund is booked as a
        negative contribution to the original contributor). We negate the
        amount so the refund reports as a positive magnitude.

    Args:
        entity: Entity name to resolve (federal committee / state filer).
        year: Optional calendar year of the refund (None = all years).
        min_amount: Only include recipients whose aggregated refund total
            from this entity is >= this (None = no threshold).
        limit: Max recipient rows to return (default 200).

    Returns:
        ``{entity, matched, total, record_count, by_source, by_year,
        returns: [{source, recipient, purpose, amount, count}]}`` sorted by
        amount desc. As with the other entity tools, total/by_source/by_year/
        record_count are FULL totals; `returns` is the top-N display view.
    """
    matched = _resolve_entity(entity)
    fed_ids = [m["id"] for m in matched["federal"]]
    st_ids = [m["id"] for m in matched["state"]]

    by_source = {"federal": 0.0, "state": 0.0}
    by_year: dict[str, float] = {}
    record_count = 0
    returns: list[dict[str, Any]] = []

    if fed_ids:
        yc, yp = _year_clause("contribution_date", year)
        hparams = {"ids": fed_ids, **yp}
        if min_amount is not None:
            hparams["min_amount"] = min_amount
        # One UNION of the two federal contribution tables, refunds only
        # (negative). Negate so the refund magnitude is positive.
        neg_union = (
            f"SELECT coalesce(nullif(contributor_name,''), other_committee_id, "
            f"'UNKNOWN') AS nm, -contribution_amount AS amt, contribution_date AS dt "
            f"FROM fec.fec_individual_contributions "
            f"WHERE committee_id = ANY(:ids) AND contribution_amount < 0 {yc} "
            f"UNION ALL "
            f"SELECT coalesce(nullif(contributor_name,''), other_committee_id, "
            f"'UNKNOWN') AS nm, -contribution_amount AS amt, contribution_date AS dt "
            f"FROM fec.fec_other_contributions "
            f"WHERE committee_id = ANY(:ids) AND contribution_amount < 0 {yc}"
        )
        row = execute_read(
            f"SELECT coalesce(sum(amt),0) AS amt, count(*) AS n FROM ({neg_union}) u",
            hparams)[0]
        by_source["federal"] += _money(row.get("amt"))
        record_count += int(row.get("n") or 0)
        yr_rows = execute_read(
            f"SELECT extract(year from dt)::int AS yr, sum(amt) AS amt "
            f"FROM ({neg_union}) u GROUP BY 1", hparams)
        for r in yr_rows:
            if r.get("yr") is not None:
                k = str(int(r["yr"]))
                by_year[k] = by_year.get(k, 0.0) + _money(r.get("amt"))
        having = " HAVING sum(amt) >= :min_amount" if min_amount is not None else ""
        rows = execute_read(
            f"SELECT nm AS recipient, sum(amt) AS amount, count(*) AS n "
            f"FROM ({neg_union}) u GROUP BY 1{having} ORDER BY amount DESC LIMIT :lim",
            {**hparams, "lim": limit})
        for r in rows:
            c = _coerce_row(r)
            returns.append({
                "source": "federal", "recipient": c.get("recipient"),
                "purpose": "Refund (negative contribution)",
                "amount": _money(c.get("amount")), "count": int(c.get("n") or 0),
            })

    if st_ids:
        yc, yp = _year_clause("expense_date", year)
        hparams = {"ids": st_ids, **yp}
        if min_amount is not None:
            hparams["min_amount"] = min_amount
        # amount > 0: a refund to a donor is a positive outflow. The negative
        # refund-purposed rows are amendment reversals whose payee is the
        # committee itself (e.g. "Refund of Contribution Originally Disclosed"
        # booked negative to undo a prior disclosure) — not money given back to
        # a donor, so they are excluded to keep the total meaningful.
        row = execute_read(
            f"SELECT coalesce(sum(amount),0) AS amt, count(*) AS n FROM expn_all "
            f"WHERE cmte_id = ANY(:ids) AND {RETURNED_CONTRIB_PURPOSE} "
            f"AND amount > 0 {yc}",
            hparams)[0]
        by_source["state"] += _money(row.get("amt"))
        record_count += int(row.get("n") or 0)
        yr_rows = execute_read(
            f"SELECT extract(year from expense_date)::int AS yr, sum(amount) AS amt "
            f"FROM expn_all WHERE cmte_id = ANY(:ids) AND {RETURNED_CONTRIB_PURPOSE} "
            f"AND amount > 0 {yc} GROUP BY 1", hparams)
        for r in yr_rows:
            if r.get("yr") is not None:
                k = str(int(r["yr"]))
                by_year[k] = by_year.get(k, 0.0) + _money(r.get("amt"))
        having = " HAVING sum(amount) >= :min_amount" if min_amount is not None else ""
        rows = execute_read(
            f"SELECT target AS recipient, max(purpose) AS purpose, "
            f"sum(amount) AS amount, count(*) AS n FROM ("
            f"  SELECT coalesce(nullif(payee_naml,''),'(unknown payee)') AS target, "
            f"  purpose, amount FROM expn_all "
            f"  WHERE cmte_id = ANY(:ids) AND {RETURNED_CONTRIB_PURPOSE} "
            f"  AND amount > 0 {yc}"
            f") d GROUP BY 1{having} ORDER BY amount DESC LIMIT :lim",
            {**hparams, "lim": limit})
        for r in rows:
            c = _coerce_row(r)
            returns.append({
                "source": "state", "recipient": c.get("recipient"),
                "purpose": c.get("purpose"),
                "amount": _money(c.get("amount")), "count": int(c.get("n") or 0),
            })

    returns.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "entity": entity,
        "matched": matched,
        "total": round(by_source["federal"] + by_source["state"], 2),
        "record_count": record_count,
        "by_source": {k: round(v, 2) for k, v in by_source.items()},
        "by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "returns": returns[:limit],
    }
