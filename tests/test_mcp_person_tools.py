"""Tests for the person/schema MCP tools (Phase 2).

Covers:
- payments_to_person (payee / donor / filer roles, blind-spot count)
- rapid_expense_vendors (date+amount resolution, ambiguity flag, no fan-out)
- describe_table (valid / unknown / invalid names)
- get_server_docs (serves docs/mcp_server.md)
- _person_predicate (field-aware, word-anchored matching)
- server registration (16 tools, instructions block)

Mocked dispatch (unit) + live-DB regression (skipped when Postgres is not
reachable) using the known Michael Gomez Daly / Tubbs 2026 ground truth.
"""

from datetime import date
from unittest.mock import patch

import pytest

import core.mcp.tools as tools
from core.mcp.server import INSTRUCTIONS, TOOLS, _create_server
from core.mcp.tools import (
    _person_predicate,
    describe_table,
    get_server_docs,
    payments_to_person,
    rapid_expense_vendors,
)


# ------------------------------------------------------------------ #
#  Live-DB availability (module-level, computed once)
# ------------------------------------------------------------------ #


def _live_db_up() -> bool:
    try:
        from core.mcp.db import execute_read

        execute_read("SELECT 1 AS x")
        return True
    except Exception:
        return False


LIVE = pytest.mark.skipif(not _live_db_up(), reason="no live Postgres available")


# ------------------------------------------------------------------ #
#  Mock DB
# ------------------------------------------------------------------ #


class MockDB:
    """Deterministic mock of execute_read for the four new tools."""

    def __init__(self):
        self.calls: list[dict] = []

    def query(self, sql, params=None):
        from datetime import datetime

        norm = " ".join(sql.upper().split())
        self.calls.append({"sql": norm, "params": params or {}})

        # -- committee name resolution (filer_xref -> filername) ---------- #
        if "FROM FILER_XREF_CD X" in norm:
            return [
                {
                    "naml": "Tubbs for Lieutenant Governor 2026",
                    "namf": "",
                    "namt": "",
                    "nams": "Friends of",
                    "city": "San Francisco",
                    "st": "CA",
                    "filer_type": "PC",
                    "status": "Active",
                }
            ]
        # -- filer resolution (xref_id -> filer_id) ------------------------ #
        if "FROM FILER_XREF_CD WHERE XREF_ID" in norm:
            return [{"filer_id": 1479071}]

        # -- payments_to_person: payee rows (window total) ----------------- #
        if "COUNT(*) OVER () AS TOTAL_MATCHES" in norm:
            return [
                {
                    "expn_date": datetime(2026, 5, 21),
                    "amount": 354.65,
                    "purpose": "POSTAGE FOR DOORHANGERS",
                    "payee_name": "GOMEZ DALY MICHAEL",
                    "filer_id": 1479071,
                    "committee": "TUBBS FOR LIEUTENANT GOVERNOR 2026",
                    "cmte_id": "C9900001",
                    "total_matches": 3,
                },
                {
                    "expn_date": None,
                    "amount": 5000.0,
                    "purpose": "REIMBURSEMENTS",
                    "payee_name": "Daly Michael",
                    "filer_id": 999,
                    "committee": None,
                    "cmte_id": None,
                    "total_matches": 3,
                },
            ]
        # -- payments_to_person: blind spot (s496 count) ------------------- #
        # (the rapid_expense total-count query shares this prefix; it is
        #  distinguished by the filer_id = :fid equality below)
        if (
            norm.startswith("SELECT COUNT(*) AS N FROM S496_CD")
            and "WHERE FILER_ID = :FID" not in norm
        ):
            return [{"n": 29}]
        # -- payments_to_person: donor count + de-duped rows --------------- #
        if norm.startswith("SELECT COUNT(*) AS N FROM RECEIPTS_ALL"):
            return [{"n": 0}]
        if "FROM SEQ S" in norm and "DONOR_NAME" in norm:
            return []
        # -- payments_to_person: filer matches ------------------------------ #
        if "FROM FILERNAME_CD FN" in norm:
            return [{"filer_id": 1364490, "name": "DALY MICHAEL J.",
                     "cmte_id": "1364490"}]

        # -- rapid_expense_vendors: total count ----------------------------- #
        if norm.startswith("SELECT COUNT(*) AS N FROM S496_CD"):
            return [{"n": 3}]
        # -- rapid_expense_vendors: resolved lines -------------------------- #
        if "ARRAY_AGG(P.PAYEE" in norm:
            return [
                {"d": date(2026, 5, 21), "amount": 354.65,
                 "dscr": "POSTAGE", "payees": ["GOMEZ DALY MICHAEL",
                                               "SOME OTHER VENDOR"]},
                {"d": date(2026, 5, 6), "amount": 455.93,
                 "dscr": "POSTAGE FOR DOORHANGERS", "payees": []},
                {"d": date(2026, 4, 21), "amount": 6500.0,
                 "dscr": "DOORHANGERS (ESTIMATE)", "payees": None},
            ]
        # -- rapid_expense_vendors: description roll-up for unresolved lines - #
        # the roll-up is the only query starting with the CTE prefix; the
        # resolved-lines query (with ARRAY_AGG) is matched above first
        if norm.startswith("WITH F AS ( SELECT DISTINCT FILING_ID"):
            return [
                {
                    "description": "TELEVISION ADS",
                    "occurrences": 4,
                    "total": 18000.0,
                    "first_seen": date(2026, 1, 15),
                    "last_seen": date(2026, 6, 3),
                },
            ]

        # -- describe_table -------------------------------------------------- #
        if "FROM INFORMATION_SCHEMA.COLUMNS" in norm:
            if (params or {}).get("t") == "nope_nope":
                return []
            return [
                {"column_name": "filing_id", "data_type": "integer",
                 "nullable": True},
                {"column_name": "amount", "data_type": "numeric",
                 "nullable": True},
                {"column_name": "ctrib_date", "data_type": "timestamp",
                 "nullable": True},
            ]
        if "FROM PG_CLASS" in norm:
            return [{"n": 1392112}]
        if "FROM INFORMATION_SCHEMA.TABLES" in norm:
            return [{"table_name": "rcpt_cd"}, {"table_name": "expn_cd"}]

        raise AssertionError(f"unexpected SQL in mock: {norm[:120]}")


# ------------------------------------------------------------------ #
#  _person_predicate
# ------------------------------------------------------------------ #


class TestPersonPredicate:
    def test_full_name_builds_cross_field_branches(self):
        clause, params = _person_predicate(
            "e.payee_naml", "e.payee_namf", "Michael Gomez Daly"
        )
        # word-anchored params for last-first, first-last, and both
        # single-field orders
        assert params["pl_last"] == r"\mDaly"
        assert params["pl_first"] == r"\mMichael"
        assert params["pl_lastfirst"].startswith(r"\mDaly\b")
        assert params["pl_firstlast"].startswith(r"\mMichael")
        # middle initials are optional: first param uses the first token
        assert "Gomez" not in params["pl_first"]
        assert clause.count(" OR ") == 3

    def test_last_name_only(self):
        clause, params = _person_predicate("e.payee_naml", "e.payee_namf", "Daly")
        assert params == {"pl_last": r"\mDaly"}
        assert clause.count(" OR ") == 1

    def test_comma_input(self):
        _, params = _person_predicate(
            "e.payee_naml", "e.payee_namf", "Daly, Michael"
        )
        assert params["pl_last"] == r"\mDaly"
        assert params["pl_first"] == r"\mMichael"

    def test_regex_metacharacters_escaped(self):
        _, params = _person_predicate(
            "e.payee_naml", "e.payee_namf", "Michael O'Brien"
        )
        assert params["pl_last"] == r"\mO'Brien"  # apostrophe: no escape needed
        _, params = _person_predicate(
            "e.payee_naml", "e.payee_namf", "Michael D.O"
        )
        assert params["pl_last"] == r"\mD\.O"

    def test_word_anchor_defeats_false_friends(self):
        """\\m anchoring means 'Daly' cannot match inside 'Odalys'.

        (Postgres ARE; verified against live data in the regression tests.)
        """
        _, params = _person_predicate("e.payee_naml", "e.payee_namf", "Daly")
        assert params["pl_last"].startswith("\\m")


# ------------------------------------------------------------------ #
#  payments_to_person (mocked)
# ------------------------------------------------------------------ #


class TestPaymentsToPersonMocked:
    def test_payee_role_shape_and_blind_spot(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = payments_to_person("Michael Gomez Daly", roles="payee")
        assert r["person"] == "Michael Gomez Daly"
        assert r["payee"]["total"] == 3  # from the window function
        assert len(r["payee"]["payments"]) == 2
        p0 = r["payee"]["payments"][0]
        assert p0["amount"] == 354.65
        assert p0["payee_name"] == "GOMEZ DALY MICHAEL"
        assert "TUBBS" in p0["committee"]
        assert p0["cmte_id"] == "C9900001"
        # blind spot reported because payee total > 0
        assert r["blind_spot"]["s496_lines_for_paying_committees"] == 29
        # donor/filer roles not requested -> absent
        assert "donor" not in r
        assert "filer" not in r

    def test_all_roles(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = payments_to_person("Michael Gomez Daly",
                                   since_date=date(2016, 1, 1))
        assert r["since"] == "2016-01-01"
        assert r["payee"]["total"] == 3
        assert r["donor"]["total"] == 0
        assert r["donor"]["gifts"] == []
        assert [f["name"] for f in r["filer"]["matches"]] == ["DALY MICHAEL J."]
        # since param actually passed through
        payee_call = next(
            c for c in m.calls if "TOTAL_MATCHES" in c["sql"]
        )
        assert payee_call["params"].get("since") == date(2016, 1, 1)

    def test_no_blind_spot_when_unpaid(self):
        def fake(sql, params=None):
            u = " ".join(sql.upper().split())
            if u.startswith("SELECT COUNT(*) AS N FROM S496_CD"):
                return [{"n": 0}]
            if u.startswith("SELECT COUNT(*) AS N FROM RECEIPTS_ALL"):
                return [{"n": 0}]
            return []

        with patch.object(tools, "execute_read", fake):
            r = payments_to_person("Nobody Here", roles="payee,donor")
        assert r["payee"]["total"] == 0
        assert r["donor"]["total"] == 0
        assert r["donor"]["gifts"] == []
        assert "blind_spot" not in r


# ------------------------------------------------------------------ #
#  rapid_expense_vendors (mocked)
# ------------------------------------------------------------------ #


class TestRapidExpenseVendorsMocked:
    def test_resolution_ambiguity_and_no_fanout(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = rapid_expense_vendors("C9900001")
        assert r["committee_id"] == "C9900001"
        assert r["total_lines"] == 3
        # exactly one row per 24-hour line (no fan-out)
        assert r["returned"] == 3
        assert len(r["resolved"]) + len(r["unresolved"]) == 3
        amb = [x for x in r["resolved"] if x["ambiguous"]]
        assert len(amb) == 1  # the two-candidate POSTAGE line
        assert amb[0]["payee"] == "GOMEZ DALY MICHAEL"  # first candidate
        unres = {x["amount"] for x in r["unresolved"]}
        assert unres == {455.93, 6500.0}
        # resolution pct over the true denominator
        assert r["resolution_pct"] == pytest.approx(100.0 / 3, abs=0.5)

    def test_unresolvable_committee(self):
        m = MockDB()
        with patch.object(tools, "execute_read",
                          lambda sql, params=None: []):
            r = rapid_expense_vendors("C0000000")
        assert r["total_lines"] == 0
        assert r["note"]

    def test_committee_name_resolved(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = rapid_expense_vendors("C9900001")
        assert "Tubbs" in r["committee"]


# ------------------------------------------------------------------ #
#  describe_table (mocked)
# ------------------------------------------------------------------ #


class TestDescribeTableMocked:
    def test_valid_table(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = describe_table("s497_cd")
        assert r["table"] == "s497_cd"
        assert r["approx_rows"] == 1392112
        assert [c["name"] for c in r["columns"]] == [
            "filing_id", "amount", "ctrib_date"
        ]
        assert r["notes"]  # curated gotcha present for s497_cd
        assert "amt_rcvd" in r["notes"]  # the actual gotcha, verbatim

    def test_unknown_table_lists_available(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = describe_table("nope_nope")
        assert "error" in r
        assert r["available"] == ["rcpt_cd", "expn_cd"]

    def test_invalid_name_rejected_without_sql(self):
        m = MockDB()
        with patch.object(tools, "execute_read", m.query):
            r = describe_table("x; DROP TABLE rcpt_cd")
        assert "error" in r
        assert m.calls == []  # rejected before any query


# ------------------------------------------------------------------ #
#  get_server_docs
# ------------------------------------------------------------------ #


class TestGetServerDocs:
    def test_serves_repo_doc(self):
        doc = get_server_docs()
        assert doc.startswith("# Campaign Finance Database")
        # catalog mentions every registered tool
        for t in TOOLS:
            assert t in doc, f"get_server_docs missing tool {t}"

    def test_fallback_when_file_missing(self):
        with patch("pathlib.Path.read_text",
                   side_effect=OSError("gone")):
            doc = get_server_docs()
        assert "Campaign Finance" in doc
        assert "payments_to_person" in doc


# ------------------------------------------------------------------ #
#  server registration
# ------------------------------------------------------------------ #


class TestServerRegistration:
    def test_sixteen_tools(self):
        assert len(TOOLS) == 19  # 16 Phase-1/2 tools + total_expenditures,
        # refunds_to_donors, data_freshness (caveats-gap fixes)
        for t in (
            "payments_to_person",
            "rapid_expense_vendors",
            "describe_table",
            "run_sql",
            "get_server_docs",
        ):
            assert t in TOOLS

    def test_instructions_block(self):
        low = INSTRUCTIONS.lower()
        assert "get_server_docs" in low
        assert "last-first" in low
        assert "blind spot" in low or "blind_spot" in low
        _create_server()  # raises if any tool registration is broken


# ------------------------------------------------------------------ #
#  Live-DB regression (Michael Gomez Daly / Tubbs 2026 ground truth)
# ------------------------------------------------------------------ #


class TestLiveGroundTruth:
    @LIVE
    def test_gomez_daly_payee_since_2016(self):
        r = payments_to_person("Michael Gomez Daly",
                               since_date=date(2016, 1, 1), roles="payee")
        assert r["payee"]["total"] == 3
        amounts = {p["amount"] for p in r["payee"]["payments"]}
        assert amounts == {455.93, 687.04, 354.65}
        assert all("TUBBS" in (p["committee"] or "")
                   for p in r["payee"]["payments"])
        assert r["blind_spot"]["s496_lines_for_paying_committees"] >= 1

    @LIVE
    def test_gomez_daly_all_time_includes_retainer_years(self):
        r = payments_to_person("Michael Gomez Daly", roles="payee")
        # 3 payments since 2016 + the 2012 measure-campaign retainer block
        assert r["payee"]["total"] >= 10
        firsts = [p["date"] for p in r["payee"]["payments"]
                  if p["date"] and p["date"] < "2016"]
        assert any(d.startswith("2012") for d in firsts)

    @LIVE
    def test_false_friends_excluded(self):
        r = payments_to_person("Daly", since_date=date(2016, 1, 1),
                               roles="payee")
        names = [p["payee_name"] for p in r["payee"]["payments"]]
        assert all("Odalys" not in n and "Brendalyn" not in n
                   for n in names if n)

    @LIVE
    def test_describe_s497_columns(self):
        d = describe_table("s497_cd")
        cols = {c["name"] for c in d["columns"]}
        assert "amount" in cols and "ctrib_date" in cols
        assert "amt_rcvd" not in cols  # that is the s498 name
        assert d["notes"]

    @LIVE
    def test_rapid_expense_no_fanout(self):
        r = rapid_expense_vendors("1479071")
        assert r["total_lines"] > 0
        assert r["returned"] == min(200, r["total_lines"])
        assert 0 <= r["resolution_pct"] <= 100
        # the POSTAGE lines resolve to the Gomez Daly payee
        assert any(x["payee"] == "GOMEZ DALY MICHAEL" for x in r["resolved"])
