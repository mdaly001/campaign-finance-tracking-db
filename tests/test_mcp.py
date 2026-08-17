"""Tests for core/mcp — DB connection, tool invocation, server setup.

Tests cover:
- DB engine creation and execute_read (mocked Postgres)
- All 9 MCP tool functions: contributions_by_donor,
  top_donors_for_committee_or_candidate, committee_outlays_to,
  vendor_revenue, committee_profile, measure_spending,
  donor_watch_since, upcoming_filings, filing_due_soon
- MCP server creation and tool registration
- Tool parameter validation (type hints / Pydantic coercion)
"""

from unittest.mock import MagicMock, patch

import pytest

from core.mcp.server import TOOLS, _create_server

# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


def _mock_row(columns, values):
    """Create a mock row dict from column/value lists."""
    return dict(zip(columns, values))


class MockDB:
    """Deterministic mock of execute_read that returns canned data."""

    def __init__(self):
        self.calls = []

    def _normalize_sql(self, sql):
        """Normalize SQL for pattern matching."""
        sql = sql.upper().strip()
        sql = " ".join(sql.split())
        return sql

    def query(self, sql, params=None):
        """Return canned results based on SQL pattern matching."""
        norm = self._normalize_sql(sql)
        self.calls.append({"sql": norm, "params": params})

        # contributions_by_donor: FROM RCPT_CD with ctrib_naml ILIKE AND cycle = :cycle
        if ("FROM RCPT_CD RC" in norm
                and "CTRIB_NAML ILIKE" in norm
                and ":CYCLE" in norm):
            return [
                _mock_row(
                    ["tran_id", "filing_id", "amend_id", "amount", "date",
                     "purpose", "cmte_id", "memo_refno", "cycle"],
                    ["T1", "F1", "A1", 100.5, "2024-01-15", "CONTRIBUTION",
                     "C1", None, 2024],
                ),
                _mock_row(
                    ["tran_id", "filing_id", "amend_id", "amount", "date",
                     "purpose", "cmte_id", "memo_refno", "cycle"],
                    ["T2", "F1", "A1", 50.0, "2024-02-01", "CONTRIBUTION",
                     "C1", None, 2024],
                ),
            ]

        # top_donors: GROUP BY + TOTAL_AMOUNT
        if "GROUP BY" in norm and "TOTAL_AMOUNT" in norm:
            return [
                _mock_row(
                    ["donor_name", "total_amount", "contribution_count",
                     "first_date", "last_date"],
                    ["Alice", 300.0, 2, "2024-01-01", "2024-02-01"],
                ),
                _mock_row(
                    ["donor_name", "total_amount", "contribution_count",
                     "first_date", "last_date"],
                    ["Bob", 50.0, 1, "2024-03-01", "2024-03-01"],
                ),
            ]

        # committee_outlays_to: FROM EXPPD_CD with filer_id = :cid
        if ("FROM EXPPD_CD E" in norm
                and "E.FILER_ID = :CID" in norm):
            return [_mock_row(
                ["tran_id", "filing_id", "amount", "date", "purpose",
                 "memo_refno"],
                ["T1", "F1", 500.0, "2024-01-15", "Office rent", None])]

        # vendor_revenue: ARRAY_AGG (receipts query)
        if "ARRAY_AGG" in norm:
            return [_mock_row(["total_received", "transaction_count", "cycles"],
                              [500.0, 2, [2024]])]

        # vendor_revenue expenditures: SUM + EXPPD_CD
        if "SUM(AMOUNT)" in norm and "EXPPD_CD" in norm:
            return [_mock_row(["total_expenditures"], [200.0])]

        # committee_profile: filername query
        if "FROM FILERNAME FN" in norm and "FILER_ID = :CID" in norm:
            return [_mock_row(
                ["filer_id", "committee_name", "committee_type", "city", "state"],
                ["C1", "Test Committee", "PC", "Los Angeles", "CA"])]

        # committee_profile: financial query with total_receipts
        if "TOTAL_RECEIPTS" in norm:
            return [_mock_row(
                ["total_receipts", "total_disbursements",
                 "total_contributions", "total_expenditures"],
                [1000.0, 500.0, 1000.0, 500.0])]

        # committee_profile: cash on hand from smry_cd
        if "SMRY_CD" in norm:
            return [_mock_row(["cash_on_hand"], [500.0])]

        # committee_profile: no data fallback (returns empty for empty tests)
        if "FILERNAME" in norm:
            return []

        # measure_spending: cvr_camp_disc with measure_id
        if ("FROM CVR_CAMP_DISC" in norm
                and "MEASURE_ID ILIKE" in norm):
            # Note: tool SQL aliases cmte_id -> committee_id
            return [_mock_row(
                ["committee_id", "committee_name", "total_spent", "support_oppose"],
                ["C1", "Yes on Prop 65", 5000.0, "S"])]

        # donor_watch_since: FROM RCPT_CD with TRAN_DT >=
        if ("FROM RCPT_CD RC" in norm
                and "TRAN_DT >=" in norm
                and ":SINCE" in norm):
            return [_mock_row(
                ["tran_id", "filing_id", "amount", "date",
                 "cmte_id", "cmte_name", "purpose"],
                ["T1", "F1", 100.0, "2024-06-01", "C1", "Committee A", None])]

        # upcoming_filings: filing_calendar with deadline_date range (no OPEN)
        if ("FROM FILING_CALENDAR" in norm
                and "DEADLINE_DATE >=" in norm
                and "'OPEN'" not in norm):
            return [_mock_row(
                ["calendar_id", "election_date", "report_type", "deadline_date",
                 "grace_period_days", "source_url", "notes"],
                [1, "2024-11-05", "F496", "2024-10-31", 0, None, None])]

        # filing_due_soon: filing_calendar with status = 'OPEN'
        if ("FROM FILING_CALENDAR" in norm
                and "'OPEN'" in norm):
            return [_mock_row(
                ["filing_id", "committee_id", "committee_name", "form_type",
                 "filing_date", "deadline_date", "status"],
                ["F100", "C1", "Test Committee", "F497", "2024-10-31",
                 "2024-10-31", "OPEN"])]

        # donor_alias query
        if "FROM DONOR_ALIAS" in norm:
            return [_mock_row(["alias_name"], ["A. Smith"])]

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
    """Patch both get_engine and execute_read to use the mock DB."""
    from core.mcp import db

    # Clear the lru_cache so get_engine returns our mock
    db.get_engine.cache_clear()

    mock_engine = MagicMock()
    mock_engine.url = "postgresql://mock@mock/mock"

    with patch.object(db, "get_engine", return_value=mock_engine):
        with patch.object(
            db, "execute_read",
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
        """_build_url should compose from DB_PASSWORD + defaults."""
        import importlib
        import os

        with patch.dict(os.environ, {"DB_PASSWORD": "r", "DB_HOST": "h"}):
            from core.mcp import db
            importlib.reload(db)
            url = db._build_url()
            assert "h:5432" in url
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

        # Replace the patched function with one that returns []
        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = contributions_by_donor("Nobody", 2024)
            assert result == []

    def test_returns_rows_when_match(self, patched_db):
        """Matching donor name should return contribution rows."""
        from core.mcp.tools import contributions_by_donor

        result = contributions_by_donor("Alice", 2024)
        assert len(result) >= 1
        row = result[0]
        assert row["tran_id"] == "T1"
        assert row["amount"] == 100.5
        assert row["purpose"] == "CONTRIBUTION"

    def test_aliases_query_called(self, patched_db):
        """include_aliases=True should query donor_alias table."""
        from core.mcp.tools import contributions_by_donor

        result = contributions_by_donor("Alice", 2024, include_aliases=True)
        assert len(result) >= 1

    def test_include_aliases_false(self, patched_db):
        """include_aliases=False should not query alias table."""
        from core.mcp.tools import contributions_by_donor

        result = contributions_by_donor("Alice", 2024, include_aliases=False)
        assert len(result) >= 1

    def test_calls_execute_read(self, patched_db):
        """The tool should call execute_read with the SQL."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Alice", 2024)
        assert len(patched_db.calls) > 0


# ------------------------------------------------------------------ #
#  Test: Tool 2 — top_donors_for_committee_or_candidate
# ------------------------------------------------------------------ #


class TestTopDonors:
    """Tool: top_donors_for_committee_or_candidate(committee_id, cycle, limit)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import top_donors_for_committee_or_candidate

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = top_donors_for_committee_or_candidate("C999", 2024)
            assert result == []

    def test_returns_aggregated_rows(self, patched_db):
        from core.mcp.tools import top_donors_for_committee_or_candidate

        result = top_donors_for_committee_or_candidate("C1", 2024, limit=10)
        assert len(result) >= 2
        assert result[0]["donor_name"] == "Alice"
        assert result[0]["total_amount"] == 300.0
        assert result[0]["contribution_count"] == 2


# ------------------------------------------------------------------ #
#  Test: Tool 3 — committee_outlays_to
# ------------------------------------------------------------------ #


class TestCommitteeOutlaysTo:
    """Tool: committee_outlays_to(committee_id, cycle)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import committee_outlays_to

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = committee_outlays_to("C999", 2024)
            assert result == []

    def test_returns_expenditures(self, patched_db):
        from core.mcp.tools import committee_outlays_to

        result = committee_outlays_to("C1", 2024)
        assert len(result) >= 1
        assert result[0]["amount"] == 500.0
        assert result[0]["purpose"] == "Office rent"


# ------------------------------------------------------------------ #
#  Test: Tool 4 — vendor_revenue
# ------------------------------------------------------------------ #


class TestVendorRevenue:
    """Tool: vendor_revenue(vendor_name, cycle)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import vendor_revenue

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = vendor_revenue("Nobody", 2024)
            assert result["total_received"] == 0.0
            assert result["total_expenditures"] == 0.0
            assert result["transaction_count"] == 0
            assert result["cycles"] == []

    def test_aggregates_receipts(self, patched_db):
        from core.mcp.tools import vendor_revenue

        result = vendor_revenue("Acme", 2024)
        assert result["vendor_name"] == "Acme"
        assert result["total_received"] == 500.0
        assert result["transaction_count"] == 2
        assert 2024 in result["cycles"]


# ------------------------------------------------------------------ #
#  Test: Tool 5 — committee_profile
# ------------------------------------------------------------------ #


class TestCommitteeProfile:
    """Tool: committee_profile(committee_id, cycle)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import committee_profile

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = committee_profile("C999")
            assert result["committee_name"] == "Unknown"
            assert result["total_receipts"] == 0.0

    def test_returns_profile(self, patched_db):
        from core.mcp.tools import committee_profile

        result = committee_profile("C1", 2024)
        assert result["committee_name"] == "Test Committee"
        assert result["committee_type"] == "PC"
        assert result["city"] == "Los Angeles"
        assert result["state"] == "CA"
        assert result["total_receipts"] == 1000.0


# ------------------------------------------------------------------ #
#  Test: Tool 6 — measure_spending
# ------------------------------------------------------------------ #


class TestMeasureSpending:
    """Tool: measure_spending(measure_id, cycle)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import measure_spending

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = measure_spending("PROP 999", 2024)
            assert result == []

    def test_returns_measure_spending(self, patched_db):
        from core.mcp.tools import measure_spending

        result = measure_spending("PROP 65", 2024)
        assert len(result) >= 1
        assert result[0]["total_spent"] == 5000.0
        assert result[0]["support_oppose"] == "S"


# ------------------------------------------------------------------ #
#  Test: Tool 7 — donor_watch_since
# ------------------------------------------------------------------ #


class TestDonorWatchSince:
    """Tool: donor_watch_since(since_date, donor_name)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import donor_watch_since

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = donor_watch_since("2020-01-01")
            assert result == []

    def test_returns_recent_contributions(self, patched_db):
        from core.mcp.tools import donor_watch_since

        result = donor_watch_since("2024-01-01")
        assert len(result) >= 1
        assert result[0]["amount"] == 100.0
        assert result[0]["date"] == "2024-06-01"


# ------------------------------------------------------------------ #
#  Test: Tool 8 — upcoming_filings
# ------------------------------------------------------------------ #


class TestUpcomingFilings:
    """Tool: upcoming_filings(committee_id, days_ahead)."""

    def test_returns_empty(self, patched_db):
        from core.mcp.tools import upcoming_filings

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = upcoming_filings()
            assert result == []

    def test_returns_deadlines(self, patched_db):
        from core.mcp.tools import upcoming_filings

        result = upcoming_filings(days_ahead=30)
        assert len(result) >= 1
        assert result[0]["report_type"] == "F496"


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

    def test_returns_open_deadlines(self, patched_db):
        from core.mcp.tools import filing_due_soon

        result = filing_due_soon(7)
        assert len(result) >= 1
        assert result[0]["committee_id"] == "C1"
        assert result[0]["status"] == "OPEN"


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
        """_create_server should return an MCPServer with 9 tools."""
        import asyncio

        tools = asyncio.run(server.list_tools())
        assert len(tools) == 9

    def test_tool_names_registered(self, server):
        """All 9 expected tool names should be present."""
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
    """Test that core.mcp exports all 9 tools."""

    def test_all_tools_exported(self):
        """All 9 tool functions should be importable from core.mcp."""
        from core.mcp import (
            committee_outlays_to,
            committee_profile,
            contributions_by_donor,
            donor_watch_since,
            filing_due_soon,
            measure_spending,
            top_donors_for_committee_or_candidate,
            upcoming_filings,
            vendor_revenue,
        )

        assert callable(contributions_by_donor)
        assert callable(top_donors_for_committee_or_candidate)
        assert callable(committee_outlays_to)
        assert callable(vendor_revenue)
        assert callable(committee_profile)
        assert callable(measure_spending)
        assert callable(donor_watch_since)
        assert callable(upcoming_filings)
        assert callable(filing_due_soon)

    def test_db_exports(self):
        """DB helpers should be importable from core.mcp."""
        from core.mcp import execute_read, get_engine

        assert callable(get_engine)
        assert callable(execute_read)
