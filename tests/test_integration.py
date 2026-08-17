"""Integration tests: verify queries work on pre-seeded sample data.

Uses the `loaded_db` fixture from conftest.py which provides an
in-memory SQLite engine with all tables created and sample data inserted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

# ------------------------------------------------------------------ #
#  Integration: Contributions query
# ------------------------------------------------------------------ #


class TestContributionsQuery:
    def test_total_contributions_for_committee(self, loaded_db):
        """Total contributions to C001 in cycle 2024 = 500 + 250 + 1000 = 1750."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT SUM(amount) as total
                    FROM rcpt_cd rc
                    JOIN filings f ON rc.filing_id = f.filing_id
                    WHERE f.election_year = 2024
                    """
                )
            ).fetchone()
            assert rows[0] == pytest.approx(1750.0)

    def test_contribution_count(self, loaded_db):
        """Should have 3 contributions for C001 in cycle 2024."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT COUNT(*) as cnt
                    FROM rcpt_cd rc
                    JOIN filings f ON rc.filing_id = f.filing_id
                    WHERE f.election_year = 2024
                    """
                )
            ).fetchone()
            assert rows[0] == 3

    def test_donor_breakdown(self, loaded_db):
        """Alice gave 750 total (500+250), Bob gave 1000."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT ctrib_naml, SUM(amount) as total
                    FROM rcpt_cd rc
                    JOIN filings f ON rc.filing_id = f.filing_id
                    WHERE f.election_year = 2024
                    GROUP BY ctrib_naml
                    ORDER BY total DESC
                    """
                )
            ).fetchall()

            names_totals = [(r[0], float(r[1])) for r in rows]
            # Bob should be first (1000 > 750)
            assert names_totals[0] == ("Bob Jones", pytest.approx(1000.0))
            assert names_totals[1] == ("Alice Smith", pytest.approx(750.0))


# ------------------------------------------------------------------ #
#  Integration: Expenditures query
# ------------------------------------------------------------------ #


class TestExpendituresQuery:
    def test_total_expenditures(self, loaded_db):
        """Total expenditures for C001 in cycle 2024 = 300 + 750 = 1050."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT SUM(amount) as total
                    FROM exppd_cd e
                    JOIN filings f ON e.filing_id = f.filing_id
                    WHERE f.election_year = 2024
                    """
                )
            ).fetchone()
            assert rows[0] == pytest.approx(1050.0)

    def test_vendor_breakdown(self, loaded_db):
        """Acme Corp received 300, Bob Jones received 750."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT payee_naml, SUM(amount) as total
                    FROM exppd_cd e
                    JOIN filings f ON e.filing_id = f.filing_id
                    WHERE f.election_year = 2024
                    GROUP BY payee_naml
                    ORDER BY total DESC
                    """
                )
            ).fetchall()

            names_totals = [(r[0], float(r[1])) for r in rows]
            assert names_totals[0] == ("Bob Jones", pytest.approx(750.0))
            assert names_totals[1] == ("Acme Corp", pytest.approx(300.0))


# ------------------------------------------------------------------ #
#  Integration: Committee profile query
# ------------------------------------------------------------------ #


class TestCommitteeProfile:
    def test_committee_exists(self, loaded_db):
        """FILERNAME should return Test Committee for C001."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text("SELECT naml FROM filername WHERE filer_id = 'C001'")
            ).fetchone()
            assert rows[0] == "Test Committee"

    def test_cash_flow(self, loaded_db):
        """Cash flow: receipts 1750, expenditures 1050 → net +700."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COALESCE(SUM(amount), 0) FROM rcpt_cd rc
                         JOIN filings f ON rc.filing_id = f.filing_id
                         WHERE f.election_year = 2024)
                        -
                        (SELECT COALESCE(SUM(amount), 0) FROM exppd_cd e
                         JOIN filings f ON e.filing_id = f.filing_id
                         WHERE f.election_year = 2024)
                        AS net_cash
                    """
                )
            ).fetchone()
            assert rows[0] == pytest.approx(700.0)


# ------------------------------------------------------------------ #
#  Integration: Ballot measures query
# ------------------------------------------------------------------ #


class TestBallotMeasuresQuery:
    def test_measure_found(self, loaded_db):
        """PROP 15 should be in ballot_measures."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text("SELECT measure_name FROM ballot_measures WHERE measure_no = 'PROP 15'")
            ).fetchone()
            assert rows[0] == "Property Tax Initiative"

    def test_measure_jurisdiction(self, loaded_db):
        """PROP 15 should be Statewide."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text("SELECT jurisdiction FROM ballot_measures WHERE measure_no = 'PROP 15'")
            ).fetchone()
            assert rows[0] == "Statewide"


# ------------------------------------------------------------------ #
#  Integration: Filing calendar query
# ------------------------------------------------------------------ #


class TestFilingCalendarQuery:
    def test_deadline_found(self, loaded_db):
        """F496 deadline for 2024-11-05 election should be 2024-10-21."""
        with loaded_db.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT deadline_date FROM filing_calendar "
                    "WHERE report_type = 'F496' "
                    "AND election_date = '2024-11-05'"
                )
            ).fetchone()
            assert rows[0] == "2024-10-21"

    def test_deadline_count(self, loaded_db):
        """Should have exactly 1 filing calendar entry."""
        with loaded_db.connect() as conn:
            rows = conn.execute(text("SELECT COUNT(*) FROM filing_calendar")).fetchone()
            assert rows[0] == 1
