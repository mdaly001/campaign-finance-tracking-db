"""Tests for the fuzzy_name_search MCP tool.

The tool's selling points are its correctness guarantees for weak/naive
clients, so this file pins the SQL contract and the guarantee surface:

- it queries ONLY deduplicated views (receipts_all over the rcpt/s497/s498
  dedup views, expn_cd_deduped) — never base tables, so amended/duplicated
  rows cannot double-count,
- totals_by_year cover ALL matching rows even when limit caps the detail
  rows (totals never skewed by the cap),
- whole-word token matching (word-boundary regex), not substring guesses,
- entity resolution: name -> filername_cd/entity_alias -> canonical entity,
  and committee/candidate modes aggregate over resolved entities only —
  an honest empty answer (with a note) when nothing resolves,
- registration in the server's tool list.

Everything runs against a fake DB (no DB needed).
"""

from decimal import Decimal

import pytest

import core.mcp.server as server_mod
import core.mcp.tools as tools
from core.mcp.tools import _all_tokens_sql, _name_tokens, _quoted_csv, fuzzy_name_search


class FakeDB:
    """Deterministic execute_read stand-in that dispatches on the FROM target."""

    def __init__(self, **over):
        self.calls: list[tuple[str, dict]] = []
        self.entities = list(over.get("entities", []))
        self.alias_rows = list(over.get("alias_rows", []))
        self.alias_filer_rows = list(over.get("alias_filer_rows", []))
        self.rcpt_detail = list(over.get("rcpt_detail", []))
        self.rcpt_totals = list(over.get("rcpt_totals", []))
        self.expn_detail = list(over.get("expn_detail", []))
        self.expn_totals = list(over.get("expn_totals", []))
        self.s496_detail = list(over.get("s496_detail", []))
        self.s496_totals = list(over.get("s496_totals", []))
        self.top_recipients = list(over.get("top_recipients", []))
        self.cmte_names = list(over.get("cmte_names", []))
        self.raise_on_alias = over.get("raise_on_alias", False)

    def query(self, sql, params=None):
        s = " ".join(sql.split())
        self.calls.append((s, params or {}))
        if "FROM entity_alias" in s:
            if self.raise_on_alias:
                raise RuntimeError("relation entity_alias does not exist")
            return list(self.alias_rows)
        if "FROM filername_cd f WHERE f.filer_id IN" in s:
            return list(self.alias_filer_rows)
        if "FROM filername_cd f" in s:
            return list(self.entities)
        if "FROM filer_xref_cd x" in s:
            return list(self.cmte_names)
        if "FROM receipts_all x" in s:
            return list(self.rcpt_totals) if "GROUP BY 1" in s else list(self.rcpt_detail)
        if "FROM expn_cd_deduped e" in s:
            if "ORDER BY SUM" in s:
                return list(self.top_recipients)
            if "GROUP BY 1" in s:
                return list(self.expn_totals)
            return list(self.expn_detail)
        if "FROM s496_cd_deduped" in s:
            return list(self.s496_totals) if "GROUP BY 1" in s else list(self.s496_detail)
        return []


def _run(db, name_query, **kwargs):
    tools.execute_read = db.query
    try:
        return fuzzy_name_search(name_query, **kwargs)
    finally:
        del tools.execute_read


def _db(monkeypatch, **over):
    fake = FakeDB(**over)
    monkeypatch.setattr(tools, "execute_read", fake.query)
    return fake


# --------------------------------------------------------------------- #
#  Registration + helpers                                               #
# --------------------------------------------------------------------- #


def test_registered_as_tool():
    assert "fuzzy_name_search" in server_mod.TOOLS
    assert server_mod._create_server() is not None


def test_name_tokens_word_boundaries():
    assert _name_tokens("O'Brien-Daly, M.") == ["O'BRIEN", "DALY", "M"]
    assert _name_tokens("quinn delaney") == ["QUINN", "DELANEY"]
    assert _name_tokens("   ") == []


def test_all_tokens_sql_and_quoting():
    assert (
        _all_tokens_sql("UPPER(x)", 2)
        == "(UPPER(x) ~* :fnq_t0 AND UPPER(x) ~* :fnq_t1)"
    )
    assert _quoted_csv(["C0695132", "O'Hara"]) == "'C0695132', 'O''Hara'"


# --------------------------------------------------------------------- #
#  Guarantees                                                           #
# --------------------------------------------------------------------- #


def test_empty_query_queries_nothing(monkeypatch):
    db = _db(monkeypatch)
    out = fuzzy_name_search("   ")
    assert db.calls == []
    assert out["resolved_via"] == "none"
    assert out["matches"] == [] and out["totals_by_year"] == {}
    assert "empty query" in out["notes"][0]


def test_only_deduped_views_queried(monkeypatch):
    db = _db(
        monkeypatch,
        entities=[],
        rcpt_totals=[{"yr": 2026, "total": Decimal("250000.00"), "n": 4}],
        expn_totals=[{"yr": 2026, "total": Decimal("1000.00"), "n": 1}],
    )
    out = fuzzy_name_search("Michael Daly", entity_type="all")
    for sql, _ in db.calls:
        for base in ("rcpt_cd", "expn_cd", "s496_cd", "s497_cd", "s498_cd"):
            assert f"FROM {base} " not in f"{sql} "  # never a base table
    assert any("FROM receipts_all" in sql for sql, _ in db.calls)
    assert any("FROM expn_cd_deduped" in sql for sql, _ in db.calls)
    # totals cover ALL rows regardless of any detail-row cap
    assert out["totals_by_year"]["2026"] == {"in": 250000.0, "out": 1000.0, "net": 249000.0, "n": 5}


def test_token_params_bound_with_word_boundaries(monkeypatch):
    db = _db(monkeypatch)
    fuzzy_name_search("O'Brien-Daly, M.")
    assert db.calls  # first query already carries the token params
    sql, params = db.calls[0]
    assert params == {"fnq_t0": r"\mO'BRIEN\y", "fnq_t1": r"\mDALY\y", "fnq_t2": r"\mM\y"}


def test_committee_mode_requires_entity_resolution(monkeypatch):
    db = _db(monkeypatch)  # no entities -> nothing resolves
    out = fuzzy_name_search("Tubbs for Lieutenant Governor 2026", entity_type="committee")
    fact_queries = [sql for sql, _ in db.calls if "receipts_all" in sql or "expn_cd_deduped" in sql]
    assert fact_queries == []  # honest empty answer, never guessed/guessed-aggregated
    assert out["resolved_via"] == "none"
    assert out["matches"] == []
    assert any("did not resolve" in n for n in out["notes"])


def test_committee_mode_aggregates_over_resolved_entities(monkeypatch):
    db = _db(
        monkeypatch,
        entities=[{"filer_id": 1224960, "xref_filer_id": "425948", "filer_type": "C",
                   "naml": "TUBBS", "namf": "LIEUTENANT GOVERNOR", "namt": None, "nams": None}],
        rcpt_totals=[{"yr": 2026, "total": Decimal("7000000.00"), "n": 3}],
        expn_totals=[{"yr": 2026, "total": Decimal("500000.00"), "n": 7}],
        expn_detail=[{
            "tbl": "expn", "source": "expn_cd", "tran_id": "T9", "filing_id": 9, "amend_id": 0,
            "txn_date": "2026-04-01", "amount": Decimal("50000.00"), "purpose": "Consulting",
            "code": "5.04", "cmte_id": "425948", "name_variant": "ACME CONSULTING",
        }],
        top_recipients=[{"recipient": "ACME PRINTING", "amount": Decimal("1200.00"), "payments": 3}],
        cmte_names=[{"xref_id": "425948", "naml": "TUBBS", "namf": "LIEUTENANT GOVERNOR",
                     "namt": None, "nams": None}],
    )
    out = fuzzy_name_search("Tubbs for Lieutenant Governor 2026", entity_type="committee")
    assert out["resolved_via"] == "entity"
    assert out["matched_entities"][0]["name"] == "TUBBS, LIEUTENANT GOVERNOR"
    assert any("IN ('425948')" in sql for sql, _ in db.calls)
    assert out["totals_by_year"]["2026"] == {"in": 7000000.0, "out": 500000.0, "net": 6500000.0, "n": 10}
    assert out["top_recipients"][0] == {"recipient": "ACME PRINTING", "amount": 1200.0, "payments": 3}
    assert out["matches"][0]["committee_name"] == "TUBBS, LIEUTENANT GOVERNOR"
    assert out["matches"][0]["source"] == "expn_cd"


def test_candidate_mode_excludes_own_filings_from_indep_path(monkeypatch):
    db = _db(
        monkeypatch,
        entities=[{"filer_id": 7, "xref_filer_id": "425948", "filer_type": "C",
                   "naml": "DIAZ", "namf": "MAYOR", "namt": None, "nams": None}],
    )
    fuzzy_name_search("Diaz for mayor", entity_type="candidate")
    cand_queries = [sql for sql, _ in db.calls if "cand_naml" in sql]
    assert cand_queries
    assert all("NOT IN ('425948')" in sql for sql in cand_queries)  # no double count


def test_alias_only_resolution_and_graceful_degradation(monkeypatch):
    db = _db(
        monkeypatch,
        entities=[],
        alias_rows=[{"alias_name": "QUINN DELANEY", "source_filer_id": 42}],
        alias_filer_rows=[{"filer_id": 42, "xref_filer_id": "42", "filer_type": "C",
                           "naml": "DELANEY", "namf": "QUINN", "namt": None, "nams": None}],
        rcpt_detail=[{
            "tbl": "rcpt", "source": "rcpt_cd", "tran_id": "T1", "filing_id": 101, "amend_id": 0,
            "txn_date": "2026-03-23", "amount": Decimal("250000.00"), "purpose": "Monetary Contribution",
            "code": None, "cmte_id": "425948", "name_variant": "DELANEY, M. QUINN",
        }],
    )
    out = fuzzy_name_search("Quinn Delaney", entity_type="all")
    assert out["resolved_via"] == "alias"
    assert out["matched_entities"][0]["filer_id"] == 42
    assert out["matched_entities"][0]["name"] == "DELANEY, QUINN"

    # entity_alias unavailable -> name-only matching still answers
    db2 = _db(monkeypatch, raise_on_alias=True)
    out2 = fuzzy_name_search("Quinn Delaney", entity_type="all")
    assert out2["resolved_via"] in ("entity", "none")  # name-only, no crash
    assert any("entity_alias unavailable" in n for n in out2["notes"])


def test_name_only_resolution_when_no_entity_resolves(monkeypatch):
    db = _db(
        monkeypatch,
        entities=[],
        alias_rows=[],
        rcpt_detail=[{
            "tbl": "rcpt", "source": "rcpt_cd", "tran_id": "T5", "filing_id": 12, "amend_id": 1,
            "txn_date": "2026-01-05", "amount": Decimal("500.00"), "purpose": "Monetary Contribution",
            "code": None, "cmte_id": "777", "name_variant": "Daly M. Michael",
        }],
        rcpt_totals=[{"yr": 2026, "total": Decimal("500.00"), "n": 1}],
    )
    out = fuzzy_name_search("Michael Daly", entity_type="donor")
    assert out["resolved_via"] == "name"
    assert out["matches"][0]["matched_name"] == "Daly M. Michael"
    assert out["totals_by_year"]["2026"] == {"in": 500.0, "out": 0.0, "net": 500.0, "n": 1}
