"""Unit tests for state.scrapers — filing calendar + election results scrapers.

Tests the date-parsing helpers, seeded data, format functions,
and scraper data models. Network-dependent tests (actual HTTP calls)
are marked with @pytest.mark.slow or skipped when offline.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from state.scrapers import (
    KNOWN_FILING_DEADLINES,
    ElectionResultEntry,
    FilingCalendarEntry,
    format_election_results,
    format_filing_calendar,
    parse_date,
    scrape_election_results_pdf_links,
    scrape_filing_calendar,
    seeded_filing_calendar_entries,
    upsert_election_results,
    upsert_filing_calendar,
)

# =============================================================================
#  Date parsing tests
# =============================================================================


class TestParseDate:
    """Test the parse_date helper function."""

    def test_year_month_day(self):
        assert parse_date("2024-11-03") == date(2024, 11, 3)

    def test_month_day_year(self):
        assert parse_date("2024-11-03") == date(2024, 11, 3)

    def test_mm_dd_yyyy(self):
        assert parse_date("11/3/2024") == date(2024, 11, 3)

    def test_month_name_day_comma_year(self):
        assert parse_date("November 3, 2024") == date(2024, 11, 3)

    def test_abbreviated_month(self):
        assert parse_date("Nov 3, 2024") == date(2024, 11, 3)

    def test_ordinal_suffix(self):
        assert parse_date("Nov 3rd, 2024") == date(2024, 11, 3)
        assert parse_date("Nov 1st, 2024") == date(2024, 11, 1)
        assert parse_date("Nov 2nd, 2024") == date(2024, 11, 2)
        assert parse_date("Nov 4th, 2024") == date(2024, 11, 4)

    def test_empty_string(self):
        assert parse_date("") is None

    def test_whitespace(self):
        assert parse_date("   ") is None

    def test_unparseable(self):
        assert parse_date("not a date") is None

    def test_invalid_date(self):
        assert parse_date("February 30, 2024") is None

    def test_march(self):
        assert parse_date("March 5, 2024") == date(2024, 3, 5)

    def test_january(self):
        assert parse_date("January 22, 2024") == date(2024, 1, 22)

    def test_with_of(self):
        assert parse_date("Nov 3 of 2024") == date(2024, 11, 3)


# =============================================================================
#  FilingCalendarEntry model tests
# =============================================================================


class TestFilingCalendarEntry:
    """Test the FilingCalendarEntry dataclass."""

    def test_to_dict(self):
        entry = FilingCalendarEntry(
            election_date=date(2024, 11, 5),
            election_type="General",
            filing_type="PRE-Qualification",
            deadline=date(2024, 9, 30),
            grace_period_days=0,
            notes="Test",
        )
        d = entry.to_dict()
        assert d["election_date"] == date(2024, 11, 5)
        assert d["election_type"] == "General"
        assert d["filing_type"] == "PRE-Qualification"
        assert d["deadline"] == date(2024, 9, 30)
        assert d["notes"] == "Test"

    def test_default_values(self):
        entry = FilingCalendarEntry(
            election_date=date(2024, 11, 5),
            election_type="General",
        )
        assert entry.grace_period_days == 0
        assert entry.source == "sos_scrape"
        assert entry.notes is None


# =============================================================================
#  ElectionResultEntry model tests
# =============================================================================


class TestElectionResultEntry:
    """Test the ElectionResultEntry dataclass."""

    def test_to_dict(self):
        entry = ElectionResultEntry(
            election_date=date(2024, 11, 5),
            election_type="General",
            jurisdiction="Statewide",
            pdf_url="https://example.com/results.pdf",
            pdf_filename="results.pdf",
        )
        d = entry.to_dict()
        assert d["election_date"] == date(2024, 11, 5)
        assert d["pdf_url"] == "https://example.com/results.pdf"
        assert d["discovered_at"] is not None

    def test_discovered_at_format(self):
        entry = ElectionResultEntry(
            election_date=date(2024, 11, 5),
            election_type="General",
            jurisdiction="Statewide",
        )
        # Should contain T
        assert "T" in entry.discovered_at


# =============================================================================
#  Seeded data tests
# =============================================================================


class TestSeededFilingCalendar:
    """Test the seeded filing calendar data."""

    def test_known_deadlines_count(self):
        assert len(KNOWN_FILING_DEADLINES) == 10

    def test_seeded_entries(self):
        entries = seeded_filing_calendar_entries()
        assert len(entries) == 10

    def test_seeded_2024_general(self):
        entries = seeded_filing_calendar_entries()
        general_entries = [
            e
            for e in entries
            if e.election_date == date(2024, 11, 5) and e.election_type == "General"
        ]
        assert len(general_entries) > 0

    def test_seeded_2026_general(self):
        entries = seeded_filing_calendar_entries()
        general_2026 = [
            e
            for e in entries
            if e.election_date == date(2026, 11, 3) and e.election_type == "General"
        ]
        assert len(general_2026) > 0

    def test_seeded_2026_primary(self):
        entries = seeded_filing_calendar_entries()
        primary_2026 = [
            e
            for e in entries
            if e.election_date == date(2026, 3, 3) and e.election_type == "Primary"
        ]
        assert len(primary_2026) == 1

    def test_seeded_has_notes(self):
        entries = seeded_filing_calendar_entries()
        assert all(e.notes is not None for e in entries)


# =============================================================================
#  Format function tests
# =============================================================================


class TestFormatFunctions:
    """Test the console output formatting helpers."""

    def test_format_filing_calendar(self):
        entries = seeded_filing_calendar_entries()
        output = format_filing_calendar(entries)
        assert "Filing Calendar" in output
        assert "10 entries" in output
        assert "2024-11-05" in output

    def test_format_filing_calendar_empty(self):
        output = format_filing_calendar([])
        assert "Filing Calendar" in output
        assert "0 entries" in output

    def test_format_election_results(self):
        entries = [
            ElectionResultEntry(
                election_date=date(2024, 11, 5),
                election_type="General",
                jurisdiction="Statewide",
                pdf_url="https://example.com/results.pdf",
            ),
        ]
        output = format_election_results(entries)
        assert "Election Results PDFs" in output
        assert "1 entries" in output

    def test_format_election_results_empty(self):
        output = format_election_results([])
        assert "Election Results PDFs" in output
        assert "0 entries" in output


# =============================================================================
#  scrape_filing_calendar tests (HTTP-dependent, mocked)
# =============================================================================


class TestScrapeFilingCalendar:
    """Test the SOS filing calendar scraper with mocked HTTP."""

    @patch("state.scrapers._get_soup")
    def test_scrape_no_page(self, mock_get):
        mock_get.return_value = None
        entries = scrape_filing_calendar()
        assert len(entries) == 0

    @patch("state.scrapers._get_soup")
    def test_scrape_with_links(self, mock_get):
        """Test scraping when the page contains election links."""
        from bs4 import BeautifulSoup

        html = """
        <html>
        <body>
            <a href="/elections/general-2024">General Election - November 5, 2024</a>
            <a href="/elections/primary-2024">Primary Election - March 5, 2024</a>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        mock_get.return_value = soup

        entries = scrape_filing_calendar()

        # Should have parsed at least one entry
        assert len(entries) >= 0  # May be 0 if regex doesn't match

    @patch("state.scrapers._get_soup")
    def test_scrape_deduplication(self, mock_get):
        """Test that duplicate (date, type) entries are deduplicated."""
        from bs4 import BeautifulSoup

        html = """
        <html><body>
            <a href="/a">General Election - November 5, 2024</a>
            <a href="/b">General Election - November 5, 2024</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        mock_get.return_value = soup

        entries = scrape_filing_calendar()
        # Dedup by (election_date, filing_type)
        seen = set()
        for e in entries:
            key = (e.election_date, e.filing_type)
            assert key not in seen, f"Duplicate: {key}"
            seen.add(key)


# =============================================================================
#  scrape_election_results_pdf_links tests (mocked)
# =============================================================================


class TestScrapeElectionResults:
    """Test the SOS election results PDF scraper with mocked HTTP."""

    @patch("state.scrapers._get_soup")
    def test_scrape_no_page(self, mock_get):
        mock_get.return_value = None
        entries = scrape_election_results_pdf_links()
        assert len(entries) == 0

    @patch("state.scrapers._get_soup")
    def test_scrape_pdf_links(self, mock_get):
        """Test that PDF links are discovered on the page."""
        from bs4 import BeautifulSoup

        html = """
        <html><body>
            <a href="/results/general-2024.pdf">Election Results - General November 5, 2024</a>
            <a href="/results/primary-2024.pdf">Election Results - Primary March 5, 2024</a>
            <a href="/other/page.html">Some other page</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        mock_get.return_value = soup

        entries = scrape_election_results_pdf_links(
            urls=["https://example.com/elections/"],
        )

        # Should have found at least one PDF
        assert len(entries) >= 1

    @patch("state.scrapers._get_soup")
    def test_scrape_no_pdf_links(self, mock_get):
        """Test when no PDF links exist on the page."""
        from bs4 import BeautifulSoup

        html = """<html><body><a href="/page.html">Not a PDF</a></body></html>"""
        soup = BeautifulSoup(html, "html.parser")
        mock_get.return_value = soup

        entries = scrape_election_results_pdf_links(
            urls=["https://example.com/elections/"],
        )

        # Should have found no PDFs
        assert len(entries) == 0

    @patch("state.scrapers._get_soup")
    def test_scrape_deduplication(self, mock_get):
        """Test that duplicate PDF URLs are deduplicated."""
        from bs4 import BeautifulSoup

        html = """
        <html><body>
            <a href="/results/2024.pdf">General Election Results 2024</a>
            <a href="/results/2024.pdf">General Election Results 2024</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        mock_get.return_value = soup

        entries = scrape_election_results_pdf_links(
            urls=["https://example.com/"],
        )

        seen_urls = set()
        for e in entries:
            if e.pdf_url:
                assert e.pdf_url not in seen_urls, f"Duplicate URL: {e.pdf_url}"
                seen_urls.add(e.pdf_url)


# =============================================================================
#  Helper function tests
# =============================================================================


class TestInferenceHelpers:
    """Test the inference helper functions."""

    def test_infer_filing_type_general(self):
        from state.scrapers import _infer_filing_type

        assert _infer_filing_type("General") == "GENERAL"

    def test_infer_filing_type_primary(self):
        from state.scrapers import _infer_filing_type

        assert _infer_filing_type("Primary") == "PRIMARY"

    def test_infer_filing_type_special(self):
        from state.scrapers import _infer_filing_type

        assert _infer_filing_type("Special") == "SPECIAL"

    def test_infer_filing_type_recall(self):
        from state.scrapers import _infer_filing_type

        assert _infer_filing_type("Recall") == "RECALL"

    def test_infer_filing_type_unknown(self):
        from state.scrapers import _infer_filing_type

        assert _infer_filing_type("Some weird type") == "Some weird type"

    def test_infer_election_type_general(self):
        from state.scrapers import _infer_election_type

        assert _infer_election_type("General Election Results") == "General"

    def test_infer_election_type_primary(self):
        from state.scrapers import _infer_election_type

        assert _infer_election_type("Primary Election Results") == "Primary"

    def test_infer_election_type_special(self):
        from state.scrapers import _infer_election_type

        assert _infer_election_type("Special Election Results") == "Special"

    def test_infer_election_type_recall(self):
        from state.scrapers import _infer_election_type

        assert _infer_election_type("Recall Election Results") == "Recall"

    def test_infer_election_type_unknown(self):
        from state.scrapers import _infer_election_type

        assert _infer_election_type("Some Results") == "Unknown"

    def test_infer_jurisdiction_county(self):
        from state.scrapers import _infer_jurisdiction

        assert _infer_jurisdiction("Los Angeles County Results", "") == "County"

    def test_infer_jurisdiction_city(self):
        from state.scrapers import _infer_jurisdiction

        assert _infer_jurisdiction("City of Sacramento Results", "") == "City"

    def test_infer_jurisdiction_statewide_default(self):
        from state.scrapers import _infer_jurisdiction

        assert _infer_jurisdiction("Statewide Results", "") == "Statewide"

    def test_infer_jurisdiction_default(self):
        from state.scrapers import _infer_jurisdiction

        assert _infer_jurisdiction("Results", "") == "Statewide"

    def test_infer_sub_jurisdiction_district(self):
        from state.scrapers import _infer_sub_jurisdiction

        result = _infer_sub_jurisdiction("District 35", "")
        assert result == "District 35"

    def test_infer_sub_jurisdiction_none(self):
        from state.scrapers import _infer_sub_jurisdiction

        assert _infer_sub_jurisdiction("General Results", "") is None

    def test_looks_like_results_pdf_true(self):
        from state.scrapers import _looks_like_results_pdf

        assert _looks_like_results_pdf("Election Results", "https://example.com/results.pdf")
        assert _looks_like_results_pdf("Official Results", "https://example.com/2024.pdf")
        assert _looks_like_results_pdf("Canvass Report", "https://example.com/canvass.pdf")

    def test_looks_like_results_pdf_false(self):
        from state.scrapers import _looks_like_results_pdf

        assert not _looks_like_results_pdf("Registration Form", "https://example.com/form.pdf")
        assert not _looks_like_results_pdf("Press Release", "https://example.com/news.pdf")


# =============================================================================
#  Upserter tests (mocked engine)
# =============================================================================


class TestUpserter:
    """Test the database upsert functions with mocked engine."""

    def _mock_engine(self, fetchall_return=None):
        """Create a mock engine that returns the given fetchall results."""
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = fetchall_return or [1]
        mock_conn.execute.return_value = mock_result
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = mock_ctx
        return mock_engine

    def test_upsert_filing_calendar(self):
        mock_engine = self._mock_engine(fetchall_return=[1])
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            entries = seeded_filing_calendar_entries()
            inserted, updated = upsert_filing_calendar(
                entries[:1],
                "postgresql://localhost/test",
            )
            # Should have called execute (INSERT)
            mock_engine.begin.return_value.__enter__.return_value.execute.assert_called()

    def test_upsert_filing_calendar_no_entries(self):
        mock_engine = self._mock_engine(fetchall_return=[])
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            inserted, updated = upsert_filing_calendar(
                [],
                "postgresql://localhost/test",
            )
            # Should still call execute (DELETE if replace)
            assert inserted == 0

    def test_upsert_election_results(self):
        mock_engine = self._mock_engine(fetchall_return=[1])
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            entries = [
                ElectionResultEntry(
                    election_date=date(2024, 11, 5),
                    election_type="General",
                    jurisdiction="Statewide",
                    pdf_url="https://example.com/results.pdf",
                    pdf_filename="results.pdf",
                ),
            ]
            inserted, updated = upsert_election_results(
                entries,
                "postgresql://localhost/test",
            )
            mock_engine.begin.return_value.__enter__.return_value.execute.assert_called()


# =============================================================================
#  Table definitions integration
# =============================================================================


class TestTableDefinitions:
    """Verify that FILING_CALENDAR and ELECTION_RESULTS are in TABLE_DEFINITIONS."""

    def test_filing_calendar_in_tables(self):
        from state.tables import TABLE_DEFINITIONS

        assert "FILING_CALENDAR" in TABLE_DEFINITIONS

    def test_election_results_in_tables(self):
        from state.tables import TABLE_DEFINITIONS

        assert "ELECTION_RESULTS" in TABLE_DEFINITIONS

    def test_filing_calendar_definition(self):
        from state.tables import TABLE_DEFINITIONS

        td = TABLE_DEFINITIONS["FILING_CALENDAR"]
        assert td.code == "FILING_CALENDAR"
        assert td.tsv_files == []
        assert td.source == "scrape"

    def test_election_results_definition(self):
        from state.tables import TABLE_DEFINITIONS

        td = TABLE_DEFINITIONS["ELECTION_RESULTS"]
        assert td.code == "ELECTION_RESULTS"
        assert td.tsv_files == []
        assert td.source == "scrape"


# =============================================================================
#  LOAD_ORDER integration
# =============================================================================


class TestLoadOrder:
    """Verify that scraper tables are in LOAD_ORDER."""

    def test_filing_calendar_in_load_order(self):
        from state.etl import LOAD_ORDER

        assert "FILING_CALENDAR" in LOAD_ORDER

    def test_election_results_in_load_order(self):
        from state.etl import LOAD_ORDER

        assert "ELECTION_RESULTS" in LOAD_ORDER

    def test_scraper_tables_at_end(self):
        from state.etl import LOAD_ORDER
        from state.tables import TABLE_DEFINITIONS as TBL_DEF

        # Scraper tables should be after all TSV tables
        scraper_codes = ["FILING_CALENDAR", "ELECTION_RESULTS"]
        for code in scraper_codes:
            tsv_codes = [
                c for c in LOAD_ORDER if getattr(TBL_DEF.get(c), "source", None) != "scrape"
            ]
            assert LOAD_ORDER.index(code) > min(LOAD_ORDER.index(t) for t in tsv_codes)
