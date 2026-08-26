"""Tests for core/mcp — DB connection, tool invocation, server setup.

Tests cover:
- DB engine creation and execute_read (mocked Postgres)
- All 10 MCP tool functions against the real CAL-ACCESS schema
  (rcpt_cd / expn_cd / smry_cd / cvr_campaign_disclosure_cd /
  filername_cd / filer_xref_cd / filing_period_cd / filing_calendar /
  entity_alias)
- MCP server creation and tool registration
- Tool parameter validation (type hints / Pydantic coercion)

The mock DB dispatches on normalized SQL patterns matching the real
column names, so a regression to an invented column name fails loudly.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.mcp.server import TOOLS, _create_server

# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


def _mock_row(columns, values):
    """Create a mock row dict from column/value lists."""
    return dict(zip(columns, values))


def _soon(days: int) -> str:
    """ISO date N days from today (inside the default look-ahead windows)."""
    return (date.today() + timedelta(days=days)).isoformat()


class MockDB:
    """Deterministic mock of execute_read returning canned real-schema rows."""

    def __init__(self):
        self.calls: list[dict] = []

    def _normalize_sql(self, sql: str) -> str:
        sql = sql.upper().strip()
        return " ".join(sql.split())

    def query(self, sql, params=None):
        """Return canned results based on SQL pattern matching."""
        norm = self._normalize_sql(sql)
        self.calls.append({"sql": norm, "params": params})

        # -- alias lookup (entity_alias, scraper-owned) --------------- #
        if "FROM ENTITY_ALIAS" in norm:
            return [_mock_row(["alias_name"], ["A. Smith Inc"])]

        # -- committee name resolution (filer_xref -> filername) ------ #
        if "FROM FILER_XREF_CD X" in norm:
            return [
                _mock_row(
                    ["naml", "namf", "namt", "nams", "city", "st", "filer_type", "status"],
                    ["Test", "Jane", "M", "", "Los Angeles", "CA", "PC", "Active"],
                )
            ]

        # -- committee filer resolution (xref_id -> filer_id) --------- #
        if "FROM FILER_XREF_CD WHERE XREF_ID" in norm:
            return [_mock_row(["filer_id"], [4242])]

        # -- measure metadata (ballot_measures_cd) -------------------- #
        if "FROM BALLOT_MEASURES_CD" in norm:
            return [
                _mock_row(
                    ["measure_no", "measure_name", "measure_short_name",
                     "election_date", "jurisdiction"],
                    ["65", "California Environmental Protection Act",
                     "Prop 65", "2026-11-03", "Statewide"],
                )
            ]

        # -- measure spenders (cvr_campaign_disclosure ⋈ smry) -------- #
        if "FROM CVR_CAMPAIGN_DISCLOSURE_CD C" in norm:
            return [
                _mock_row(
                    ["candidate", "committee_name", "sup_opp_cd", "total", "filings"],
                    ["", "Yes on Prop 65", "S", 5000.0, 3],
                ),
                _mock_row(
                    ["candidate", "committee_name", "sup_opp_cd", "total", "filings"],
                    ["Doe, Jane", "No on Prop 65", "O", 3000.0, 2],
                ),
            ]

        # -- filing period deadlines (filing_period_cd) --------------- #
        if "FROM FILING_PERIOD_CD" in norm:
            return [
                _mock_row(
                    ["period_id", "period_desc", "start_date", "end_date", "deadline"],
                    [1450, "Quarterly Report Period", "2026-04-01", "2026-06-30", _soon(10)],
                )
            ]

        # -- scraper filing calendar (filing_calendar) ---------------- #
        if "FROM FILING_CALENDAR" in norm:
            return [
                _mock_row(
                    ["report_type", "election_date", "deadline_date",
                     "grace_period_days", "source_url"],
                    ["F497", "2026-11-03", _soon(5), 15, "https://sos.ca.gov"],
                )
            ]

        # -- rcpt_cd queries (distinguish by shape) ------------------- #
        if "FROM RCPT_CD" in norm:
            if "GROUP BY" in norm and ":FILER" in norm:
                # top_donors aggregation
                return [
                    _mock_row(["donor_name", "contributions", "total"],
                              ["Alice Smith", 2, 300.0]),
                    _mock_row(["donor_name", "contributions", "total"],
                              ["Bob Jones", 1, 50.0]),
                ]
            if "SUM(AMOUNT)" in norm and ":CYCLE" not in norm and ":SINCE" not in norm:
                # committee_profile rcpt totals
                return [_mock_row(["total", "n"], [1000.0, 7])]
            if ":SINCE" in norm:
                # donor_watch_since
                return [
                    _mock_row(
                        ["tran_id", "filing_id", "amend_id", "amount", "rcpt_date",
                         "purpose", "cmte_id", "memo_refno", "donor_name"],
                        ["T9", 42, 0, 100.0, "2026-06-01", None, "C1", None, "Alice Smith"],
                    )
                ]
            if ":CYCLE" in norm:
                # contributions_by_donor
                return [
                    _mock_row(
                        ["tran_id", "filing_id", "amend_id", "cycle", "amount",
                         "rcpt_date", "purpose", "cmte_id", "memo_refno", "donor_name"],
                        ["T1", 42, 0, 2026, 100.5, "2026-01-15", "CONTRIBUTION", "C1", None, "Alice Smith"],
                    ),
                    _mock_row(
                        ["tran_id", "filing_id", "amend_id", "cycle", "amount",
                         "rcpt_date", "purpose", "cmte_id", "memo_refno", "donor_name"],
                        ["T2", 43, 0, 2026, 50.0, "2026-02-01", None, "C1", "M1", "Acme Corp"],
                    ),
                ]

        # -- expn_cd queries (distinguish by shape) ------------------- #
        if "FROM EXPN_CD" in norm:
            if "FROM FILER_TO_FILER_TYPE_CD" in norm and "~* :VENDOR" in norm:
                # committees_paying_vendor aggregation
                return [
                    _mock_row(
                        ["committee", "cmte_id", "filer_category", "payments", "total"],
                        ["Test Committee", "C1234", 40002, 3, 1500.0],
                    ),
                    _mock_row(
                        ["committee", "cmte_id", "filer_category", "payments", "total"],
                        ["Other Committee", "C5678", 0, 2, 700.0],
                    ),
                ]
            if "GROUP BY" in norm and ":FILER" not in norm:
                # vendor_revenue aggregation
                return [
                    _mock_row(["vendor_name", "payments", "total"],
                              ["Acme Consulting", 2, 5000.0]),
                ]
            if "SUM(AMOUNT)" in norm and ":CYCLE" not in norm:
                # committee_profile expn totals
                return [_mock_row(["total", "n"], [800.0, 4])]
            if ":CYCLE" in norm:
                # committee_outlays_to
                return [
                    _mock_row(
                        ["expn_date", "amount", "purpose", "payee_name",
                         "cmte_id", "tran_id", "memo_refno"],
                        ["2026-03-10", 500.0, "Office rent", "Acme Consulting", "C1", "E1", None],
                    )
                ]

        # -- committee search (filername ⋈ filer_xref) ---------------- #
        if "FROM FILERNAME_CD" in norm:
            return [
                _mock_row(
                    ["cmte_id", "filer_id", "committee_name",
                     "committee_type", "status", "city"],
                    ["C1234", 4242, "Test Committee",
                     "Recipient Committee", "Active", "Los Angeles"],
                )
            ]

        # -- committee_profile last activity --------------------------- #
        if "AS LAST_ACTIVITY" in norm:
            return [_mock_row(["last_activity"], ["2026-05-01"])]

        # Fallback: no data
        return []


# Global mock DB instance for the patched tests
_mock_db = MockDB()


@pytest.fixture(autouse=True)
def reset_mock():
    """Reset the mock DB before each test."""
    _mock_db.calls.clear()
    yield _mock_db


@pytest.fixture
def patched_db(reset_mock):
    """Patch execute_read in both db and tools modules to use the mock DB."""
    from core.mcp import db

    db.get_engine.cache_clear()

    mock_engine = MagicMock()
    mock_engine.url = "postgresql://mock@mock/mock"

    with patch.object(db, "get_engine", return_value=mock_engine):
        with patch.object(
            db,
            "execute_read",
            side_effect=lambda sql, params=None: _mock_db.query(sql, params),
        ):
            with patch(
                "core.mcp.tools.execute_read",
                side_effect=lambda sql, params=None: _mock_db.query(sql, params),
            ):
                yield _mock_db


# ------------------------------------------------------------------ #
#  Test: DB engine creation
# ------------------------------------------------------------------ #


class TestDB:
    """Test DB module (engine, execute_read)."""

    def test_build_url_from_env(self):
        """_build_url should return DATABASE_URL when set."""
        import importlib
        import os

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test@h/d"}):
            from core.mcp import db

            importlib.reload(db)
            assert db._build_url() == "postgresql://test@h/d"
            importlib.reload(db)

    def test_build_url_from_components(self):
        """_build_url should compose from DB_* env vars + defaults."""
        import importlib
        import os

        with patch.dict(os.environ, {"DB_PASSWORD": "pw", "DB_HOST": "h"}):
            from core.mcp import db

            importlib.reload(db)
            url = db._build_url()
            assert "h:5432" in url
            assert "cfdb_reader" in url
            importlib.reload(db)

    def test_execute_read_returns_list(self, patched_db):
        """execute_read should return a list of dicts."""
        from core.mcp.db import execute_read

        rows = execute_read("SELECT 1 AS one")
        assert isinstance(rows, list)


# ------------------------------------------------------------------ #
#  Test: Tool 1 — contributions_by_donor
# ------------------------------------------------------------------ #


class TestContributionsByDonor:
    """Tool: contributions_by_donor(donor_name, cycle, include_aliases)."""

    def test_returns_empty_when_no_data(self, patched_db):
        """No matching rows should return an empty list."""
        from core.mcp.tools import contributions_by_donor

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = contributions_by_donor("Nobody", 2026)
            assert result == []

    def test_returns_rows_when_match(self, patched_db):
        """Matching donor name should return contribution rows."""
        from core.mcp.tools import contributions_by_donor

        result = contributions_by_donor("Smith, Alice", 2026)
        assert len(result) == 2
        row = result[0]
        assert row["tran_id"] == "T1"
        assert row["amount"] == 100.5
        assert row["purpose"] == "CONTRIBUTION"
        assert row["cmte_id"] == "C1"
        assert row["cycle"] == 2026
        assert row["donor_name"] == "Alice Smith"

    def test_name_patterns_passed(self, patched_db):
        """The SQL should receive last/first_mid/full LIKE patterns."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Smith, Alice M", 2026)
        rcpt_call = next(c for c in _mock_db.calls if "FROM RCPT_CD" in c["sql"])
        assert rcpt_call["params"]["last"] == "%Smith%"
        assert rcpt_call["params"]["first_mid"] == "Alice M %"
        assert rcpt_call["params"]["full"] == "%Smith, Alice M%"

    def test_aliases_included(self, patched_db):
        """include_aliases=True should query entity_alias and add alias params."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Alice", 2026, include_aliases=True)
        alias_call = next(c for c in _mock_db.calls if "FROM ENTITY_ALIAS" in c["sql"])
        assert alias_call is not None
        rcpt_call = next(c for c in _mock_db.calls if "FROM RCPT_CD" in c["sql"])
        assert "alias0" in rcpt_call["params"]

    def test_include_aliases_false(self, patched_db):
        """include_aliases=False should not query the alias table."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Alice", 2026, include_aliases=False)
        assert not any("FROM ENTITY_ALIAS" in c["sql"] for c in _mock_db.calls)


# ------------------------------------------------------------------ #
#  Test: Tool 2 — top_donors_for_committee_or_candidate
# ------------------------------------------------------------------ #


class TestTopDonors:
    """Tool: top_donors_for_committee_or_candidate(committee_id, cycle, limit)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import top_donors_for_committee_or_candidate

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = top_donors_for_committee_or_candidate("C999", 2026)
            assert result == []

    def test_returns_aggregated_rows(self, patched_db):
        from core.mcp.tools import top_donors_for_committee_or_candidate

        result = top_donors_for_committee_or_candidate("C1", 2026, limit=10)
        assert len(result) == 2
        assert result[0]["donor_name"] == "Alice Smith"
        assert result[0]["total"] == 300.0
        assert result[0]["contributions"] == 2


# ------------------------------------------------------------------ #
#  Test: Tool 3 — committee_outlays_to
# ------------------------------------------------------------------ #


class TestCommitteeOutlaysTo:
    """Tool: committee_outlays_to(committee_id, vendor_name, cycle, limit)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import committee_outlays_to

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = committee_outlays_to("C999", "Nobody", 2026)
            assert result == []

    def test_returns_expenditures(self, patched_db):
        from core.mcp.tools import committee_outlays_to

        result = committee_outlays_to("C1", "Acme", 2026)
        assert len(result) == 1
        row = result[0]
        assert row["amount"] == 500.0
        assert row["purpose"] == "Office rent"
        assert row["payee_name"] == "Acme Consulting"
        assert row["cmte_id"] == "C1"


# ------------------------------------------------------------------ #
#  Test: Tool 4 — vendor_revenue
# ------------------------------------------------------------------ #


class TestVendorRevenue:
    """Tool: vendor_revenue(vendor_name, limit)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import vendor_revenue

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = vendor_revenue("Nobody")
            assert result == []

    def test_aggregates_expenditures(self, patched_db):
        from core.mcp.tools import vendor_revenue

        result = vendor_revenue("Acme")
        assert len(result) == 1
        assert result[0]["vendor_name"] == "Acme Consulting"
        assert result[0]["total"] == 5000.0
        assert result[0]["payments"] == 2


# ------------------------------------------------------------------ #
#  Test: Tool 4b — committees_paying_vendor
# ------------------------------------------------------------------ #


class TestCommitteesPayingVendor:
    """Tool: committees_paying_vendor(vendor_name, limit, candidate_only)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import committees_paying_vendor

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = committees_paying_vendor("Nobody")
            assert result == []

    def test_ranks_committees_and_flags_candidates(self, patched_db):
        from core.mcp.tools import committees_paying_vendor

        result = committees_paying_vendor("Google")
        assert len(result) == 2
        # sorted by total descending
        assert result[0]["committee"] == "Test Committee"
        assert result[0]["cmte_id"] == "C1234"
        assert result[0]["total"] == 1500.0
        assert result[0]["is_candidate"] is True  # category 40002
        assert result[1]["committee"] == "Other Committee"
        assert result[1]["is_candidate"] is False  # category 0

    def test_candidate_only_adds_category_filter(self, patched_db):
        from core.mcp.tools import committees_paying_vendor

        committees_paying_vendor("Google", candidate_only=True)
        sqls = [c["sql"] for c in patched_db.calls]
        assert any("AND FCL.CATEGORY = 40002" in s for s in sqls)

    def test_no_category_filter_by_default(self, patched_db):
        from core.mcp.tools import committees_paying_vendor

        committees_paying_vendor("Google")
        sqls = [c["sql"] for c in patched_db.calls]
        assert not any("AND FCL.CATEGORY = 40002" in s for s in sqls)


# ------------------------------------------------------------------ #
#  Test: Tool 5 — committee_profile
# ------------------------------------------------------------------ #


class TestCommitteeProfile:
    """Tool: committee_profile(committee_id, as_of_date)."""

    def test_returns_zero_profile_when_no_data(self, patched_db):
        from core.mcp.tools import committee_profile

        # Real Postgres: GROUP-BY-less aggregates always return one (zero) row;
        # only the name-lookup and GREATEST queries can be empty/null.
        def _zero_aggregates(sql, params=None):
            s = sql.upper()
            if "SUM(AMOUNT)" in s:
                return [{"total": 0, "n": 0}]
            if "AS LAST_ACTIVITY" in s:
                return [{"last_activity": None}]
            return []

        with patch("core.mcp.tools.execute_read", side_effect=_zero_aggregates):
            result = committee_profile("C999")
            assert result is not None
            assert result["committee_name"] is None
            assert result["total_contributions"] == 0.0
            assert result["total_expenditures"] == 0.0

    def test_returns_profile(self, patched_db):
        from core.mcp.tools import committee_profile

        result = committee_profile("C1")
        assert result["committee_name"] == "Test, Jane M"
        assert result["committee_type"] == "PC"
        assert result["status"] == "Active"
        assert result["city"] == "Los Angeles"
        assert result["state"] == "CA"
        assert result["total_contributions"] == 1000.0
        assert result["contribution_count"] == 7
        assert result["total_expenditures"] == 800.0
        assert result["expenditure_count"] == 4
        assert result["as_of_date"] is None

    def test_as_of_date_passes_param(self, patched_db):
        from core.mcp.tools import committee_profile

        result = committee_profile("C1", as_of_date=date(2026, 1, 1))
        assert result["as_of_date"] == "2026-01-01"
        rcpt_call = next(
            c for c in _mock_db.calls
            if "FROM RCPT_CD" in c["sql"] and "SUM(AMOUNT)" in c["sql"]
        )
        assert rcpt_call["params"]["asof"] == date(2026, 1, 1)
        assert "RCPT_DATE <= :ASOF" in rcpt_call["sql"]


# ------------------------------------------------------------------ #
#  Test: find_committees
# ------------------------------------------------------------------ #


class TestFindCommittees:
    """Tool: find_committees(name, limit)."""

    def test_returns_matches(self, patched_db):
        from core.mcp.tools import find_committees

        result = find_committees("test")
        assert len(result) == 1
        assert result[0]["cmte_id"] == "C1234"
        assert result[0]["filer_id"] == 4242
        assert result[0]["committee_name"] == "Test Committee"
        assert result[0]["status"] == "Active"

    def test_name_and_limit_passed(self, patched_db):
        from core.mcp.tools import find_committees

        find_committees("Becerra", limit=5)
        call = next(c for c in _mock_db.calls if "FROM FILERNAME_CD" in c["sql"])
        assert call["params"]["q"] == "%Becerra%"
        assert call["params"]["lim"] == 5

    def test_returns_empty_when_no_match(self, patched_db):
        from core.mcp.tools import find_committees

        with patch("core.mcp.tools.execute_read", return_value=[]):
            assert find_committees("zzz-no-such") == []


# ------------------------------------------------------------------ #
#  Test: Tool 6 — measure_spending
# ------------------------------------------------------------------ #


class TestMeasureSpending:
    """Tool: measure_spending(measure_id, limit)."""

    def test_returns_empty_when_unknown(self, patched_db):
        from core.mcp.tools import measure_spending

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = measure_spending("999")
            assert result == []

    def test_returns_measure_totals(self, patched_db):
        from core.mcp.tools import measure_spending

        result = measure_spending("65")
        assert len(result) == 1
        row = result[0]
        assert row["measure_no"] == "65"
        assert row["measure_name"] == "California Environmental Protection Act"
        assert row["total_reported"] == 8000.0
        assert len(row["top_committees"]) == 2
        assert row["top_committees"][0]["committee_name"] == "Yes on Prop 65"
        assert row["top_committees"][0]["sup_opp"] == "S"
        assert row["top_committees"][0]["total"] == 5000.0


# ------------------------------------------------------------------ #
#  Test: Tool 7 — donor_watch_since
# ------------------------------------------------------------------ #


class TestDonorWatchSince:
    """Tool: donor_watch_since(donor_name, since_date, include_aliases)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import donor_watch_since

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = donor_watch_since("Nobody", date(2026, 1, 1))
            assert result == []

    def test_returns_recent_contributions(self, patched_db):
        from core.mcp.tools import donor_watch_since

        result = donor_watch_since("Smith", date(2026, 1, 1))
        assert len(result) == 1
        assert result[0]["amount"] == 100.0
        assert result[0]["date"] == "2026-06-01"
        assert result[0]["donor_name"] == "Alice Smith"

    def test_since_param_passed(self, patched_db):
        from core.mcp.tools import donor_watch_since

        donor_watch_since("Smith", date(2026, 1, 1), include_aliases=False)
        rcpt_call = next(c for c in _mock_db.calls if "FROM RCPT_CD" in c["sql"])
        assert rcpt_call["params"]["since"] == date(2026, 1, 1)
        assert "RCPT_DATE >= :SINCE" in rcpt_call["sql"]


# ------------------------------------------------------------------ #
#  Test: Tool 8 — upcoming_filings
# ------------------------------------------------------------------ #


class TestUpcomingFilings:
    """Tool: upcoming_filings(committee_id, days_ahead)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import upcoming_filings

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = upcoming_filings("C1")
            assert result == []

    def test_returns_period_deadlines(self, patched_db):
        from core.mcp.tools import upcoming_filings

        result = upcoming_filings("C1", days_ahead=30)
        assert len(result) == 1
        row = result[0]
        assert row["period_desc"] == "Quarterly Report Period"
        assert row["committee_id"] == "C1"
        assert row["committee_name"] == "Test, Jane M"
        assert row["days_until"] == 10
        assert "DEADLINE >= :TODAY" in next(
            c for c in _mock_db.calls if "FROM FILING_PERIOD_CD" in c["sql"]
        )["sql"]


# ------------------------------------------------------------------ #
#  Test: Tool 9 — filing_due_soon
# ------------------------------------------------------------------ #


class TestFilingDueSoon:
    """Tool: filing_due_soon(days_ahead)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import filing_due_soon

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = filing_due_soon()
            assert result == []

    def test_returns_calendar_deadlines(self, patched_db):
        from core.mcp.tools import filing_due_soon

        result = filing_due_soon(days_ahead=7)
        assert len(result) == 1
        row = result[0]
        assert row["report_type"] == "F497"
        assert row["grace_period_days"] == 15
        assert row["days_until"] == 5
        assert row["deadline_date"] == _soon(5)


# ------------------------------------------------------------------ #
#  Test: MCP server creation
# ------------------------------------------------------------------ #


class TestServer:
    """Test MCP server setup and tool registration."""

    @pytest.fixture
    def server(self):
        """Create a test server."""
        return _create_server()

    def test_server_creation(self, server):
        """_create_server should return an MCPServer with 11 tools."""
        import asyncio

        tools = asyncio.run(server.list_tools())
        assert len(tools) == 11

    def test_tool_names_registered(self, server):
        """All expected tool names should be present."""
        import asyncio

        tools = asyncio.run(server.list_tools())
        registered = [t.name for t in tools]
        assert set(registered) == set(TOOLS)

    def test_tools_have_descriptions(self, server):
        """All tools should have non-empty descriptions."""
        import asyncio

        tools = asyncio.run(server.list_tools())
        for tool in tools:
            assert tool.description and len(tool.description) > 10, (
                f"Tool '{tool.name}' missing description"
            )


# ------------------------------------------------------------------ #
#  Test: Module exports
# ------------------------------------------------------------------ #


class TestExports:
    """Test that core.mcp exports all 11 tools."""

    def test_all_tools_exported(self):
        """All 11 tool functions should be importable from core.mcp."""
        from core.mcp import (
            committee_outlays_to,
            committee_profile,
            committees_paying_vendor,
            contributions_by_donor,
            donor_watch_since,
            filing_due_soon,
            find_committees,
            measure_spending,
            top_donors_for_committee_or_candidate,
            upcoming_filings,
            vendor_revenue,
        )

        assert callable(contributions_by_donor)
        assert callable(top_donors_for_committee_or_candidate)
        assert callable(committee_outlays_to)
        assert callable(vendor_revenue)
        assert callable(committees_paying_vendor)
        assert callable(committee_profile)
        assert callable(find_committees)
        assert callable(measure_spending)
        assert callable(donor_watch_since)
        assert callable(upcoming_filings)
        assert callable(vendor_revenue)

    def test_db_exports(self):
        """DB helpers should be importable from core.mcp."""
        from core.mcp import execute_read, get_engine

        assert callable(get_engine)
        assert callable(execute_read)
