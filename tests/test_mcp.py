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

        # -- receipts_all queries (rcpt_cd + 24-hr s497_cd + s498_cd;
        #    distinguish by shape) ------------------------------------- #
        if "FROM RECEIPTS_ALL" in norm:
            if "AS CONTRIBUTIONS" in norm:
                # top_donors aggregation (24-hr deduped)
                return [
                    _mock_row(["donor_name", "contributions", "total"],
                              ["Alice Smith", 2, 300.0]),
                    _mock_row(["donor_name", "contributions", "total"],
                              ["Bob Jones", 1, 50.0]),
                ]
            if "SUM(KEEP_N" in norm and ":CYCLE" not in norm and ":SINCE" not in norm:
                # committee_profile receipt totals (24-hr deduped)
                return [_mock_row(["total", "n"], [1000.0, 7])]
            if ":SINCE" in norm:
                # donor_watch_since
                return [
                    _mock_row(
                        ["source", "tran_id", "filing_id", "amend_id", "amount",
                         "rcpt_date", "purpose", "cmte_id", "memo_refno", "donor_name"],
                        ["rcpt_cd", "T9", 42, 0, 100.0, "2026-06-01", None, "C1",
                         None, "Alice Smith"],
                    )
                ]
            if ":CYCLE" in norm:
                # contributions_by_donor
                return [
                    _mock_row(
                        ["source", "tran_id", "filing_id", "amend_id", "cycle",
                         "amount", "rcpt_date", "purpose", "cmte_id",
                         "memo_refno", "donor_name"],
                        ["rcpt_cd", "T1", 42, 0, 2026, 100.5, "2026-01-15",
                         "CONTRIBUTION", "C1", None, "Alice Smith"],
                    ),
                    _mock_row(
                        ["source", "tran_id", "filing_id", "amend_id", "cycle",
                         "amount", "rcpt_date", "purpose", "cmte_id",
                         "memo_refno", "donor_name"],
                        ["s497_cd", "T2", 43, 0, 2026, 50.0, "2026-02-01",
                         None, "C1", "M1", "Acme Corp"],
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
            # patch.dict keeps other keys; DATABASE_URL (commonly exported
            # to run the server) would otherwise shadow the components.
            os.environ.pop("DATABASE_URL", None)
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
        # rows carry their receipts_all source (24-hr reports included)
        assert {r["source"] for r in result} == {"rcpt_cd", "s497_cd"}

    def test_name_patterns_passed(self, patched_db):
        """The SQL should receive last/first_mid/full LIKE patterns."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Smith, Alice M", 2026)
        rcpt_call = next(c for c in _mock_db.calls if "FROM RECEIPTS_ALL" in c["sql"])
        assert rcpt_call["params"]["last"] == "%Smith%"
        assert rcpt_call["params"]["first_mid"] == "Alice M %"
        assert rcpt_call["params"]["full"] == "%Smith, Alice M%"

    def test_aliases_included(self, patched_db):
        """include_aliases=True should query entity_alias and add alias params."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Alice", 2026, include_aliases=True)
        alias_call = next(c for c in _mock_db.calls if "FROM ENTITY_ALIAS" in c["sql"])
        assert alias_call is not None
        rcpt_call = next(c for c in _mock_db.calls if "FROM RECEIPTS_ALL" in c["sql"])
        assert "alias0" in rcpt_call["params"]

    def test_include_aliases_false(self, patched_db):
        """include_aliases=False should not query the alias table."""
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Alice", 2026, include_aliases=False)
        assert not any("FROM ENTITY_ALIAS" in c["sql"] for c in _mock_db.calls)


# ------------------------------------------------------------------ #
#  24-hour reporting: contribution tools query receipts_all
# ------------------------------------------------------------------ #

class TestReceiptsUnion:
    """24-hour Form 497 / Form 498 gifts live in s497_cd / s498_cd, not
    rcpt_cd; every contribution tool must read the receipts_all union
    and de-dup double-reported gifts across sources."""

    def _receipts_calls(self):
        return [c for c in _mock_db.calls if "FROM RECEIPTS_ALL" in c["sql"]]

    def test_contributions_by_donor_uses_view(self, patched_db):
        from core.mcp.tools import contributions_by_donor

        contributions_by_donor("Smith", 2026)
        assert self._receipts_calls()
        assert not any("FROM RCPT_CD" in c["sql"] for c in _mock_db.calls)

    def test_top_donors_uses_view_and_dedup(self, patched_db):
        from core.mcp.tools import top_donors_for_committee_or_candidate

        top_donors_for_committee_or_candidate("C1", 2026)
        calls = self._receipts_calls()
        assert calls
        # de-dup keeps max(rows-per-source) per (donor, date, amount)
        assert any("MAX(SRC_N)" in c["sql"] for c in calls)

    def test_committee_profile_uses_view(self, patched_db):
        from core.mcp.tools import committee_profile

        committee_profile("C1")
        calls = self._receipts_calls()
        assert len(calls) >= 2  # contribution totals + last activity

    def test_donor_watch_since_uses_view(self, patched_db):
        from core.mcp.tools import donor_watch_since

        donor_watch_since("Smith", date(2026, 1, 1))
        assert self._receipts_calls()


# ------------------------------------------------------------------ #
#  Cross-source de-dup semantics (real SQL, in-memory SQLite)
# ------------------------------------------------------------------ #

class TestRowlistDedup:
    """_rowlist_dedup_sql keeps max(rows-per-source) per (donor, date,
    amount), preferring rcpt_cd rows. Verified against a real SQL engine
    with a receipts_all-shaped fixture, so the window/CTE syntax and the
    keep-rule itself are exercised, not just the SQL text."""

    @pytest.fixture()
    def dedup_engine(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE receipts_all (
                    src TEXT,
                    filing_id INTEGER,
                    amend_id INTEGER,
                    tran_id TEXT,
                    receipt_date TEXT,
                    amount NUMERIC,
                    donor_naml TEXT,
                    donor_namf TEXT,
                    ctrib_dscr TEXT,
                    cmte_id TEXT,
                    memo_refno TEXT,
                    donor_key TEXT,
                    donor_name TEXT
                )
                """
            ))
            conn.execute(text(
                """
                INSERT INTO receipts_all VALUES
                -- ACLU $200k gift reported on BOTH a 24-hour report and
                -- the later periodic report -> must count once, and the
                -- rcpt_cd row is preferred
                ('rcpt_cd', 100, 0, 'T1', '2026-04-03', 200000,
                 'ACLU OF NORTHERN CALIFORNIA', '', NULL, 'C100', NULL,
                 'ACLU OF NORTHERN CALIFORNIA', 'ACLU OF NORTHERN CALIFORNIA'),
                ('s497_cd', 101, 0, 'T2', '2026-04-03', 200000,
                 'ACLU OF NORTHERN CALIFORNIA', '', NULL, NULL, NULL,
                 'ACLU OF NORTHERN CALIFORNIA', 'ACLU OF NORTHERN CALIFORNIA'),
                -- SEIU: two DIFFERENT $12.5k gifts the same day, disclosed
                -- only on 24-hour reports -> both must survive
                ('s497_cd', 102, 0, 'T3', '2026-08-06', 12500,
                 'SEIU', '', NULL, NULL, NULL, 'SEIU', 'SEIU'),
                ('s497_cd', 103, 0, 'T4', '2026-08-06', 12500,
                 'SEIU', '', NULL, NULL, NULL, 'SEIU', 'SEIU'),
                -- periodic-only individual gift
                ('rcpt_cd', 104, 0, 'T5', '2026-05-01', 500,
                 'JONES', 'BOB', NULL, NULL, NULL, 'JONES BOB', 'JONES BOB')
                """
            ))
        return engine

    def _run(self, engine):
        from sqlalchemy import text

        from core.mcp.tools import _rowlist_dedup_sql

        sql = _rowlist_dedup_sql(
            "r AS (SELECT * FROM receipts_all)",
            "SELECT s.src, s.filing_id, s.amount",
            "s.receipt_date DESC, s.filing_id",
            100,
        )
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql))]

    def test_double_reported_gift_counts_once(self, dedup_engine):
        rows = self._run(dedup_engine)
        aclu = [r for r in rows if r["amount"] == 200000]
        assert len(aclu) == 1
        assert aclu[0]["src"] == "rcpt_cd"  # periodic row preferred

    def test_multi_gift_same_day_survives(self, dedup_engine):
        rows = self._run(dedup_engine)
        seiu = [r for r in rows if r["amount"] == 12500]
        assert len(seiu) == 2
        assert {r["filing_id"] for r in seiu} == {102, 103}

    def test_row_count_and_totals(self, dedup_engine):
        rows = self._run(dedup_engine)
        # 1 (ACLU) + 2 (SEIU) + 1 (Jones) = 4 rows, not 5 naive
        assert len(rows) == 4
        assert sum(float(r["amount"]) for r in rows) == 225500.0


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
            # SUM(KEEP_N ...) = receipts_all 24-hr-deduped contribution
            # totals; SUM(AMOUNT) = expn_cd totals.
            if "SUM(KEEP_N" in s or "SUM(AMOUNT)" in s:
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
            if "FROM RECEIPTS_ALL" in c["sql"] and "SUM(KEEP_N" in c["sql"]
        )
        assert rcpt_call["params"]["asof"] == date(2026, 1, 1)
        assert "RECEIPT_DATE <= :ASOF" in rcpt_call["sql"]


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
        rcpt_call = next(c for c in _mock_db.calls if "FROM RECEIPTS_ALL" in c["sql"])
        assert rcpt_call["params"]["since"] == date(2026, 1, 1)
        assert "RECEIPT_DATE >= :SINCE" in rcpt_call["sql"]


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
        """_create_server should return an MCPServer with all registered tools."""
        import asyncio

        tools = asyncio.run(server.list_tools())
        # Count is derived from the TOOLS list so it stays correct as tools
        # are added (19 as of the caveats-gap fixes).
        assert len(tools) == len(TOOLS)
        assert len(tools) == 19

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


# ------------------------------------------------------------------ #
#  Tests: data_caveats fixes — filing-scoped expenditure tools,
#  refunds tool, and data_freshness
# ------------------------------------------------------------------ #


class TestTotalExpendituresFilingScoped:
    """Tool: total_expenditures(committee_id, cycle, exclude_refunds=False).

    The aggregate must be scoped by filing ownership through
    filer_filings_cd (data_caveats.md §3.3: cmte_id on detail lines is
    NOT the filing owner), never by ``e.cmte_id = :committee_id``.
    """

    def test_scoped_by_filer_not_cmte_id(self, patched_db):
        from core.mcp.tools import total_expenditures

        result = total_expenditures("C001", 2024)
        # mock rows carry no aggregate keys -> zero-shaped result
        assert result == {
            "transaction_count": 0,
            "total_amount": 0.0,
            "form_types": [],
        }
        agg = [
            c for c in _mock_db.calls
            if "FROM EXPN_CD_DEDUPED E" in c["sql"]
        ]
        assert agg, "expected an aggregate query against expn_cd_deduped"
        sql = agg[0]["sql"]
        assert "FF.FILER_ID = :FILER" in sql
        assert "E.CMTE_ID = :COMMITTEE_ID" not in sql
        # the xref lookup resolved the committee id to a filer id first
        assert agg[0]["params"]["filer"] == 4242

    def test_exclude_refunds_flag(self, patched_db):
        from core.mcp.tools import total_expenditures, _REFUND_DSCR_REGEX
        import re

        total_expenditures("C001", 2024)
        params = _mock_db.calls[-1]["params"]
        assert "refund_rx" not in params

        total_expenditures("C001", 2024, exclude_refunds=True)
        params = _mock_db.calls[-1]["params"]
        assert params["refund_rx"] == _REFUND_DSCR_REGEX
        # refund memo codes match; normal vendor spend does not
        assert re.search(_REFUND_DSCR_REGEX, "return of contribution")
        assert re.search(_REFUND_DSCR_REGEX, "rtn of contribution")
        assert re.search(_REFUND_DSCR_REGEX, "refund of over-contrib.")
        assert not re.search(_REFUND_DSCR_REGEX, "media advertising")


class TestExpenditureToolsFilingScoped:
    """The detail-level expenditure tools must filter through
    filer_filings_cd (via _committee_predicate), not cmte_id."""

    def test_by_vendor_scoped_by_filer(self, patched_db):
        from core.mcp.tools import expenditures_by_vendor

        expenditures_by_vendor("C001", 2024)
        call = _mock_db.calls[-1]
        assert "FF.FILER_ID = :FILER" in call["sql"]
        assert "E.CMTE_ID = :COMMITTEE_ID" not in call["sql"]

    def test_by_candidate_and_outlet_scoped_by_filer(self, patched_db):
        from core.mcp.tools import (
            expenditures_by_candidate,
            expenditures_by_outlet,
        )

        expenditures_by_candidate("C001", 2024, candidate_name="Tubbs")
        call = _mock_db.calls[-1]
        assert "FF.FILER_ID = :FILER" in call["sql"]
        assert call["params"]["candidate"] == "%Tubbs%"

        expenditures_by_outlet("C001", 2024)
        call = _mock_db.calls[-1]
        assert "FF.FILER_ID = :FILER" in call["sql"]


class TestRefundsToDonors:
    """Tool: refunds_to_donors(committee_id, cycle, limit=50)."""

    def test_returns_shape_and_regex_param(self, patched_db):
        from core.mcp.tools import refunds_to_donors, _REFUND_DSCR_REGEX

        result = refunds_to_donors("C001", 2024)
        assert result["committee_id"] == "C001"
        # mock resolves any xref to filer 4242 -> committee resolved
        assert result["committee"] == "Test, Jane M"
        # the mock's canned rows do not carry the aggregate keys the
        # tool reads, so the summary is zero and the payee group falls
        # back to "(unknown payee)"
        assert result["refund_lines"] == 0
        assert result["total_refunded"] == 0.0
        # every data query must bind the refund regex as a parameter
        # (parameterized, never string-interpolated into SQL)
        data_calls = [
            c for c in _mock_db.calls if c["params"] and "refund_rx" in c["params"]
        ]
        assert data_calls, "expected a refunds query bound to the regex"
        assert all(c["params"]["refund_rx"] == _REFUND_DSCR_REGEX for c in data_calls)

    def test_unresolvable_committee_returns_empty(self, patched_db):
        from core.mcp.tools import refunds_to_donors

        with patch("core.mcp.tools.execute_read", return_value=[]):
            result = refunds_to_donors("NOT_AN_ID", 2024)
        assert result["committee"] is None
        assert result["refund_lines"] == 0
        assert "could not be resolved" in result["note"]


class TestDataFreshness:
    """Tool: data_freshness() — snapshot freshness + hygiene counts."""

    def test_shape_with_empty_db(self, patched_db):
        from core.mcp.tools import data_freshness

        result = data_freshness()
        assert result["newest_receipt_date"] is None
        assert result["newest_expenditure_date"] is None
        assert result["future_dated_rows"] == {"receipts": 0, "expenditures": 0}
        assert result["last_etl_load"] is None
        assert result["row_counts"] == {}
        assert result["notes"]  # explanatory notes always present

    def test_full_shape_with_fake_rows(self):
        import core.mcp.tools as tools
        from core.mcp.tools import data_freshness

        def fake(sql, params=None):
            u = " ".join(str(sql).upper().split())
            if u.startswith("SELECT COUNT(*) AS N, MAX(CASE WHEN RECEIPT_DATE"):
                return [{"n": 100, "newest": "2026-08-24", "future_dated": 2}]
            if u.startswith("SELECT COUNT(*) AS N, MAX(CASE WHEN EXPN_DATE"):
                return [{"n": 50, "newest": "2026-08-23", "future_dated": 1}]
            if "LOAD_CHECKPOINT" in u:
                raise AssertionError("no such table")
            if "PG_CLASS" in u:
                return [
                    {"table_name": "rcpt_cd", "approx_rows": 123456},
                    {"table_name": "expn_cd", "approx_rows": 654321},
                ]
            return []

        with patch.object(tools, "execute_read", side_effect=fake):
            r = data_freshness()
        assert r["newest_receipt_date"] == "2026-08-24"
        assert r["newest_expenditure_date"] == "2026-08-23"
        assert r["future_dated_rows"] == {"receipts": 2, "expenditures": 1}
        assert r["last_etl_load"] is None  # query raised -> graceful None
        assert any("load_checkpoint" in n for n in r["notes"])
        assert r["row_counts"] == {"rcpt_cd": 123456, "expn_cd": 654321}


class TestServerRegistrations:
    """New tools are exposed through the server registration surface."""

    def test_new_tools_registered(self):
        server = _create_server()
        import asyncio

        names = {t.name for t in asyncio.run(server.list_tools())}
        assert {
            "total_expenditures",
            "refunds_to_donors",
            "data_freshness",
        } <= names
        assert len(names) == len(TOOLS)


# ------------------------------------------------------------------ #
#  Tests: issue #2 — committee_outlays_to and the aggregate vendor tools
#  must share ONE attribution basis (filing ownership via
#  filer_filings_cd), never the detail-line cmte_id column.
# ------------------------------------------------------------------ #


class TestIssue2FilingScopedAttribution:
    """Regression tests for GitHub issue #2.

    Bug (deployed build): ``committee_outlays_to`` filtered the detail-line
    ``cmte_id`` column (``WHERE e.cmte_id = :committee_id``) while the
    aggregate (``committees_paying_vendor``) attributed payments to the filer
    that FILED the report (``JOIN filings ON filing_id``). On agent-filed
    reports the line's ``cmte_id`` carries the paying committee's id while the
    filing belongs to a different filer — so the aggregate showed non-zero
    payments for the same (committee, vendor) pair while the detail returned
    0 rows. All five tools now scope by filing ownership (the predicate
    ``WHERE e.filing_id IN (SELECT ff.filing_id FROM filer_filings_cd ff
    WHERE ff.filer_id = :filer)``), so aggregate and detail answer the same
    question from the same basis. These tests pin that invariant so a
    regression to ``cmte_id`` scoping fails loudly.
    """

    def test_committee_outlays_to_scopes_by_filing_filer(self, patched_db):
        from core.mcp import tools

        rows = tools.committee_outlays_to("C9900001", "ACME MEDIA", 2026)

        # first query resolves the committee id through filer_xref_cd
        # (the mock resolves any xref lookup to filer 4242)…
        resolve = patched_db.calls[0]
        assert "FROM FILER_XREF_CD WHERE XREF_ID = :CMTE" in resolve["sql"]
        assert resolve["params"] == {"cmte": "C9900001"}

        # …then the detail query is scoped to that filer's filings — never to
        # the cmte_id column on the detail lines.
        data = patched_db.calls[-1]
        assert "FROM EXPN_CD_DEDUPED E" in data["sql"]
        assert "FILER_FILINGS_CD FF WHERE FF.FILER_ID = :FILER" in data["sql"]
        assert "E.CMTE_ID = :COMMITTEE_ID" not in data["sql"]
        assert data["params"] == {
            "filer": 4242,
            "cycle": 2026,
            "lim": 50,
            "vendor": tools._vendor_regex("ACME MEDIA"),
        }
        # the rows the aggregate counted for this (committee, vendor) pair are
        # exactly the rows this detail query can return
        assert rows == [
            {
                "date": "2026-03-10",
                "amount": 500.0,
                "purpose": "Office rent",
                "payee_name": "Acme Consulting",
                "cmte_id": "C1",
                "tran_id": "E1",
                "memo_refno": None,
            }
        ]

    def test_expenditure_detail_tools_share_the_same_basis(self, patched_db):
        from core.mcp import tools

        for tool in (
            tools.expenditures_by_vendor,
            tools.expenditures_by_candidate,
            tools.expenditures_by_outlet,
            tools.total_expenditures,
        ):
            before = len(patched_db.calls)
            out = tool("C9900001", 2024)
            assert out is not None
            data = [
                c
                for c in patched_db.calls[before:]
                if "FROM EXPN_CD_DEDUPED E" in c["sql"]
            ]
            assert data, f"{tool.__name__} must read the deduped expn view"
            sql, params = data[-1]["sql"], data[-1]["params"]
            # scoped by the filing-owner filer…
            assert (
                "FILER_FILINGS_CD FF WHERE FF.FILER_ID = :FILER" in sql
            ), tool.__name__
            assert params["filer"] == 4242
            assert params["cycle"] == 2024
            # …never by the cmte_id column on the detail line
            assert "E.CMTE_ID = :COMMITTEE_ID" not in sql, tool.__name__

    def test_committees_paying_vendor_groups_by_filing_owner(self, patched_db):
        from core.mcp import tools

        out = tools.committees_paying_vendor("Acme Consulting")

        agg = next(
            c
            for c in patched_db.calls
            if "FROM FILER_TO_FILER_TYPE_CD" in c["sql"] and "~* :VENDOR" in c["sql"]
        )
        # attribution basis: the filing owner (JOIN filings ON filing_id), not
        # the line's cmte_id column; rows whose filing_id matches no filing
        # ("unattributed" rows) are dropped by the inner join by design so the
        # aggregate can never count a payment the detail tools cannot reach.
        assert "FROM EXPN_CD_DEDUPED E" in agg["sql"]
        assert "JOIN FILINGS FF ON FF.FILING_ID = E.FILING_ID" in agg["sql"]
        assert "E.CMTE_ID = :COMMITTEE_ID" not in agg["sql"]

        # the emitted cmte_id is the filing-owner filer's xref id — the id the
        # detail tools accept as committee_id (round trip below)
        assert out[0]["committee"] == "Test Committee"
        assert out[0]["cmte_id"] == "C1234"
        assert out[0]["is_candidate"] is True
        assert out[1]["cmte_id"] == "C5678"
        assert out[1]["is_candidate"] is False
        assert out[0]["payments"] == 3
        assert out[0]["total"] == 1500.0
        assert out[1]["payments"] == 2
        assert out[1]["total"] == 700.0

    def test_vendor_revenue_has_no_committee_filter(self, patched_db):
        from core.mcp import tools

        out = tools.vendor_revenue("Acme Consulting")

        data = [
            c for c in patched_db.calls if "FROM EXPN_CD_DEDUPED E" in c["sql"]
        ]
        assert data
        # vendor-keyed aggregate: groups over ALL filings, no committee scoping,
        # no filer parameter — the query itself cannot disagree with the detail
        # tools' scoping.
        assert "GROUP BY 1" in data[-1]["sql"]
        assert all(
            not (c["params"] and "filer" in c["params"]) for c in data
        )
        assert out == [{"vendor_name": "Acme Consulting", "payments": 2, "total": 5000.0}]

    def test_committee_id_emitted_by_aggregate_flows_to_detail(self, patched_db):
        """The cmte_id the aggregate tool emits must be a valid input for the
        detail tool and must scope it to exactly the filings the aggregate
        counted — the tools round-trip through the same id space via
        filer_xref_cd."""
        from core.mcp import tools

        agg = tools.committees_paying_vendor("Acme Consulting")
        cmte_id = agg[0]["cmte_id"]  # "C1234" from the mock aggregate row
        assert cmte_id is not None

        before = len(patched_db.calls)
        rows = tools.committee_outlays_to(cmte_id, "Acme Consulting", 2024)

        resolve = patched_db.calls[before]
        assert "FROM FILER_XREF_CD WHERE XREF_ID = :CMTE" in resolve["sql"]
        assert resolve["params"] == {"cmte": "C1234"}
        data = patched_db.calls[-1]
        assert data["params"]["filer"] == 4242  # the resolved filer's filings
        assert len(rows) == 1
        assert rows[0]["payee_name"] == "Acme Consulting"

    def test_resolve_filer_id_numeric_passthrough_and_unknown_id(self):
        from core.mcp import tools

        # with no xref rows at all, bare numeric ids still resolve to
        # themselves (they ARE filer ids); unknown non-numeric ids resolve to
        # -1, which matches no filings — an empty scope, never "everything".
        from unittest.mock import patch

        with patch.object(tools, "execute_read", return_value=[]):
            assert tools._resolve_filer_id("900532") == 900532
            assert tools._resolve_filer_id("C0695132") == -1
            assert "ff.filer_id = :filer" in tools._committee_predicate("e")
