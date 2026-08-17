"""SOS publication scrapers for filing calendar and election results.

Downloads and parses publicly available SOS election metadata:
- Filing deadlines from SOS Campaign & Lobbying pages
- Election results PDF links from SOS Elections pages

These pages do not provide structured TSV downloads, so we scrape
HTML to populate the `filing_calendar` and `election_results` tables.

Usage:
    python -m state.scrape_filing_calendar       Scrape filing deadlines
    python -m state.scrape_election_results        Scrape election PDFs

Data sources:
    - SOS Elections: https://www.sos.ca.gov/elections/
    - SOS Campaign & Lobbying: https://www.sos.ca.gov/campaign-lobbying/
    - Filing deadlines: https://www.sos.ca.gov/campaign-lobbying/helpful-resources/fines-late-filing-disclosure-statements-and-reports/
    - Election results: https://www.sos.ca.gov/elections/election-data-and-reports/
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Configuration
# ------------------------------------------------------------------ #

SOS_ELECTIONS_URL = "https://www.sos.ca.gov/elections/"
SOS_CAMPAIGN_URL = "https://www.sos.ca.gov/campaign-lobbying/campaign-filing/campaign-disclosure/"
SOS_DEADLINES_URL = (
    "https://www.sos.ca.gov/campaign-lobbying/helpful-resources/"
    "fines-late-filing-disclosure-statements-and-reports/"
)
SOS_ELECTION_RESULTS_URL = (
    "https://www.sos.ca.gov/elections/election-data-and-reports/"
)
SOS_PRIOR_ELECTIONS_URL = "https://www.sos.ca.gov/elections/previous-elections/"
SOS_UPCOMING_ELECTIONS_URL = "https://www.sos.ca.gov/elections/upcoming-elections/"

DEFAULT_TIMEOUT = 30.0
USER_AGENT = (
    "CampaignFinanceDB/1.0 (+https://github.com/mdaly001/campaign-finance-tracking-db; "
    "contact: mdaly@example.com)"
)

# ------------------------------------------------------------------ #
#  Data models
# ------------------------------------------------------------------ #


@dataclass
class FilingCalendarEntry:
    """A single filing deadline entry scraped from SOS pages."""

    election_date: date
    election_type: str
    filing_type: str | None = None
    deadline: date | None = None
    grace_period_days: int = 0
    extended_deadline: date | None = None
    source: str = "sos_scrape"
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "election_date": self.election_date,
            "election_type": self.election_type,
            "filing_type": self.filing_type,
            "deadline": self.deadline,
            "grace_period_days": self.grace_period_days,
            "extended_deadline": self.extended_deadline,
            "source": self.source,
            "notes": self.notes,
        }


@dataclass
class ElectionResultEntry:
    """A single election results PDF entry discovered on SOS pages."""

    election_date: date
    election_type: str
    jurisdiction: str
    sub_jurisdiction: str | None = None
    pdf_url: str | None = None
    pdf_filename: str | None = None
    file_size_bytes: int | None = None
    notes: str | None = None

    @property
    def discovered_at(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "election_date": self.election_date,
            "election_type": self.election_type,
            "jurisdiction": self.jurisdiction,
            "sub_jurisdiction": self.sub_jurisdiction,
            "pdf_url": self.pdf_url,
            "pdf_filename": self.pdf_filename,
            "file_size_bytes": self.file_size_bytes,
            "discovered_at": self.discovered_at,
            "notes": self.notes,
        }


# ------------------------------------------------------------------ #
#  HTTP helpers
# ------------------------------------------------------------------ #


def _get_soup(url: str, timeout: float = DEFAULT_TIMEOUT) -> BeautifulSoup | None:
    """Fetch a page and return a BeautifulSoup object, or None on failure."""
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
    except httpx.HTTPError as e:
        logger.warning("HTTP error fetching %s: %s", url, e)
        return None
    except Exception as e:
        logger.warning("Error fetching %s: %s", url, e)
        return None


def _download_pdf(url: str, dest: Path) -> Path | None:
    """Download a PDF from URL to dest, return dest path or None."""
    try:
        with httpx.Client(timeout=300.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
            logger.info("Downloaded %s (%d bytes) → %s", url, len(response.content), dest)
            return dest
    except httpx.HTTPError as e:
        logger.warning("Failed to download PDF %s: %s", url, e)
        return None


# ------------------------------------------------------------------ #
#  Filing Calendar scraper
# ------------------------------------------------------------------ #


def parse_date(text: str) -> date | None:
    """Try to parse a date from free-form text.

    Supported formats:
    - "Nov 3, 2026", "November 3, 2026"
    - "11/3/2026"
    - "2026-11-03"
    - "Nov 3rd, 2026" (ordinal suffixes)
    - "Nov 3 of 2026" ("of" instead of comma)
    """
    text = text.strip()
    if not text:
        return None

    # Strip ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
    cleaned = re.sub(r"(\d)(st|nd|rd|th)", r"\1", text)
    # Replace " of " with ", " for date parsing
    cleaned = cleaned.replace(" of ", ", ")
    if cleaned != text:
        result = parse_date(cleaned)
        if result:
            return result

    # Try YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", cleaned)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Try MM/DD/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", cleaned)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    # Try "Month DD, YYYY" or "Mon DD, YYYY"
    m = re.match(
        r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", cleaned
    )
    if m:
        month_str, day_str, year_str = m.group(1), m.group(2), m.group(3)
        try:
            import calendar

            month_map = {
                name: idx for idx, name in enumerate(calendar.month_name, 1)
            }
            month_map.update({
                "jan": 1, "feb": 2, "mar": 3, "apr": 4,
                "may": 5, "jun": 6, "jul": 7, "aug": 8,
                "sep": 9, "oct": 10, "nov": 11, "dec": 12,
                "january": 1, "february": 2, "march": 3,
                "april": 4, "june": 6, "july": 7,
                "september": 9, "october": 10,
                "november": 11, "december": 12,
            })
            month = month_map.get(month_str.lower())
            if month:
                return date(int(year_str), month, int(day_str))
        except ValueError:
            pass

    return None


def scrape_filing_calendar(url: str = SOS_DEADLINES_URL) -> list[FilingCalendarEntry]:
    """Scrape filing deadlines from the SOS page.

    This scraper looks for patterns like:
    - "Primary Election - March 5, 2024"
    - "General Election - November 3, 2026"
    - Deadline info such as "Filing deadline: January 22, 2024"

    Returns a list of FilingCalendarEntry objects.
    """
    soup = _get_soup(url)
    if not soup:
        logger.warning("Could not fetch %s", url)
        return []

    entries: list[FilingCalendarEntry] = []

    # Look for election-related headings/links
    # The SOS site uses <a> tags with election names and dates
    for a_tag in soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        if not text:
            continue

        # Look for election name patterns
        election_match = re.match(
            r"(?P<type>[^,]+?)\s*[-–—]\s*(?P<name>[^,]+),?\s*(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-z]+\s+\d{4})",
            text,
        )
        if election_match:
            etype = election_match.group("type").strip()
            _name = election_match.group("name").strip()
            date_str = election_match.group("date")

            # Clean ordinal suffixes
            date_str = re.sub(r"(\d)(st|nd|rd|th)", r"\1", date_str).strip()
            date_str = date_str.replace(" of ", " ")

            election_date = parse_date(date_str)
            if election_date is None:
                logger.debug("Could not parse date '%s' from '%s'", date_str, text)
                continue

            # Determine filing type from context
            filing_type = _infer_filing_type(etype)

            entries.append(FilingCalendarEntry(
                election_date=election_date,
                election_type=etype,
                filing_type=filing_type,
                deadline=election_date,  # SOS often lists election dates, not filing deadlines
                source="sos_page",
                notes=f"Derived from SOS page link: {text}",
            ))

    # Also look for explicit filing deadline text
    for tag in soup.find_all(["h2", "h3", "h4", "p", "li"]):
        text = tag.get_text(strip=True)
        if not text:
            continue

        # Look for "deadline" mentions
        deadline_match = re.search(
            r"deadline[:\s]+([A-Za-z]+ \d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        if deadline_match:
            deadline_str = deadline_match.group(1)
            deadline = parse_date(deadline_str)
            if deadline:
                # Find associated election date in nearby text
                parent = tag.find_parent(["h2", "h3", "h4", "section", "div"])
                election_text = parent.get_text(strip=True) if parent else ""
                election_date_match = re.search(
                    r"([A-Za-z]+ \d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})",
                    election_text,
                )
                # Extract election date from nearby text
                ed_match = election_date_match.group(1) if election_date_match else None
                election_date = parse_date(ed_match) if ed_match else None

                entries.append(FilingCalendarEntry(
                    election_date=election_date or date.today(),
                    election_type="General",
                    filing_type="FILING_DEADLINE",
                    deadline=deadline,
                    source="sos_page",
                    notes=f"Explicit deadline text: {text[:100]}",
                ))

    # Deduplicate by (election_date, filing_type)
    seen: set[tuple] = set()
    unique: list[FilingCalendarEntry] = []
    for entry in entries:
        key = (entry.election_date, entry.filing_type)
        if key not in seen:
            seen.add(key)
            unique.append(entry)

    logger.info("Scraped %d filing calendar entries from %s", len(unique), url)
    return unique


def _infer_filing_type(etype: str) -> str:
    """Map election type text to a filing type label."""
    etype_lower = etype.lower()
    if "general" in etype_lower:
        return "GENERAL"
    elif "primary" in etype_lower:
        return "PRIMARY"
    elif "special" in etype_lower:
        return "SPECIAL"
    elif "recall" in etype_lower:
        return "RECALL"
    elif "consolidated" in etype_lower:
        return "CONSOLIDATED"
    return etype.strip()


# ------------------------------------------------------------------ #
#  Election Results scraper
# ------------------------------------------------------------------ #


def scrape_election_results_pdf_links(
    urls: list[str] | None = None,
    cache_dir: Path | None = None,
    download_dir: Path | None = None,
) -> list[ElectionResultEntry]:
    """Scrape SOS pages for election results PDF links.

    Searches multiple SOS election pages:
    - https://www.sos.ca.gov/elections/
    - https://www.sos.ca.gov/elections/previous-elections/
    - https://www.sos.ca.gov/elections/upcoming-elections/

    Returns a list of ElectionResultEntry objects with discovered PDFs.
    """
    urls = urls or [
        SOS_ELECTIONS_URL,
        SOS_PRIOR_ELECTIONS_URL,
        SOS_UPCOMING_ELECTIONS_URL,
    ]

    all_entries: list[ElectionResultEntry] = []
    seen_urls: set[str] = set()

    for url in urls:
        soup = _get_soup(url)
        if not soup:
            logger.warning("Could not fetch %s — skipping", url)
            continue

        entries = _extract_pdf_links(soup, url)
        for entry in entries:
            if entry.pdf_url and entry.pdf_url not in seen_urls:
                seen_urls.add(entry.pdf_url)
                all_entries.append(entry)
                if download_dir:
                    # Download the PDF
                    filename = entry.pdf_url.split("/")[-1] or "results.pdf"
                    dest = download_dir / filename
                    if not dest.exists():
                        _download_pdf(entry.pdf_url, dest)
                        entry.file_size_bytes = dest.stat().st_size if dest.exists() else None

        logger.info(
            "Scraped %d unique PDF links from %s (total so far: %d)",
            len(entries),
            url,
            len(all_entries),
        )

    logger.info(
        "Total election result PDFs discovered: %d", len(all_entries)
    )
    return all_entries


def _extract_pdf_links(soup: BeautifulSoup, page_url: str) -> list[ElectionResultEntry]:
    """Extract PDF links from a parsed soup, returning ElectionResultEntry objects."""
    entries: list[ElectionResultEntry] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)

        # Skip non-PDF links
        if not href.lower().endswith(".pdf"):
            continue

        # Resolve relative URL
        full_url = urljoin(page_url, href)

        # Only include if it looks like an election results PDF
        if not _looks_like_results_pdf(text, full_url):
            continue

        # Parse election metadata from the link text or URL
        election_date = _parse_election_date_from_text(text)
        election_type = _infer_election_type(text)
        jurisdiction = _infer_jurisdiction(text, full_url)
        sub_jurisdiction = _infer_sub_jurisdiction(text, full_url)

        # Extract filename
        filename = full_url.split("/")[-1] if "/" in full_url else full_url
        # Clean filename
        filename = re.sub(r"[^\w\-.]", "_", filename)

        entries.append(ElectionResultEntry(
            election_date=election_date or date.today(),
            election_type=election_type,
            jurisdiction=jurisdiction,
            sub_jurisdiction=sub_jurisdiction,
            pdf_url=full_url,
            pdf_filename=filename,
            notes=f"Discovered on {page_url}",
        ))

    return entries


def _looks_like_results_pdf(text: str, url: str) -> bool:
    """Check if a link looks like it points to election results."""
    combined = f"{text} {url}".lower()
    result_keywords = [
        "election result", "voter information", "official results",
        "precinct", "vote result", "ballot measure", "canvass",
        "statewide result", "county result",
    ]
    return any(kw in combined for kw in result_keywords)


def _parse_election_date_from_text(text: str) -> date | None:
    """Try to extract an election date from link text."""
    # Match "Election on Month DD, YYYY" or "November 3, 2026"
    match = re.search(
        r"(?:election\s+(?:on\s+)?)?([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})"
        r"|\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
        text,
        re.IGNORECASE,
    )
    if match:
        if match.group(1):
            date_str = match.group(1)
            date_str = re.sub(r"(\d)(st|nd|rd|th)", r"\1", date_str).strip()
            date_str = date_str.replace(" of ", " ")
            return parse_date(date_str)
        elif match.group(2):
            try:
                return date(int(match.group(2)), int(match.group(3)), int(match.group(4)))
            except ValueError:
                pass
    return None


def _infer_election_type(text: str) -> str:
    """Infer election type from link text."""
    lower = text.lower()
    if "general" in lower:
        return "General"
    elif "primary" in lower:
        return "Primary"
    elif "special" in lower:
        return "Special"
    elif "recall" in lower:
        return "Recall"
    elif "consolidated" in lower:
        return "Consolidated"
    return "Unknown"


def _infer_jurisdiction(text: str, url: str) -> str:
    """Infer jurisdiction level from text/URL."""
    combined = f"{text} {url}".lower()
    if "county" in combined:
        return "County"
    elif "city" in combined:
        return "City"
    elif "statewide" in combined or "state" in combined:
        return "Statewide"
    return "Statewide"  # default to statewide


def _infer_sub_jurisdiction(text: str, url: str) -> str | None:
    """Infer sub-jurisdiction (e.g., district, county name) from text."""
    # Look for "District N" or "County" patterns
    match = re.search(r"(district\s+\d+|[\w\s]+county)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


# ------------------------------------------------------------------ #
#  Filing calendar pre-seeding (hardcoded known deadlines)
# ------------------------------------------------------------------ #

KNOWN_FILING_DEADLINES = [
    # Recent and upcoming known filing deadlines
    {
        "election_date": date(2024, 3, 5),
        "election_type": "Primary",
        "filing_type": "PRE-Qualification",
        "deadline": date(2024, 1, 22),
        "grace_period_days": 0,
        "notes": "2024 Primary - statement of organization deadline",
    },
    {
        "election_date": date(2024, 11, 5),
        "election_type": "General",
        "filing_type": "PRE-Qualification",
        "deadline": date(2024, 9, 30),
        "grace_period_days": 0,
        "notes": "2024 General - statement of organization deadline",
    },
    {
        "election_date": date(2024, 11, 5),
        "election_type": "General",
        "filing_type": "10-Day Report",
        "deadline": date(2024, 10, 26),
        "grace_period_days": 0,
        "notes": "2024 General - 10-day pre-election report deadline",
    },
    {
        "election_date": date(2024, 11, 5),
        "election_type": "General",
        "filing_type": "48-Hour Report",
        "deadline": date(2024, 11, 3),
        "grace_period_days": 0,
        "notes": "2024 General - 48-hour election report deadline",
    },
    {
        "election_date": date(2024, 11, 5),
        "election_type": "General",
        "filing_type": "Post-Election Report",
        "deadline": date(2024, 11, 25),
        "grace_period_days": 0,
        "notes": "2024 General - 20-day post-election report deadline",
    },
    {
        "election_date": date(2026, 3, 3),
        "election_type": "Primary",
        "filing_type": "PRE-Qualification",
        "deadline": date(2026, 1, 20),
        "grace_period_days": 0,
        "notes": "2026 Primary - statement of organization deadline",
    },
    {
        "election_date": date(2026, 11, 3),
        "election_type": "General",
        "filing_type": "PRE-Qualification",
        "deadline": date(2026, 9, 29),
        "grace_period_days": 0,
        "notes": "2026 General - statement of organization deadline",
    },
    {
        "election_date": date(2026, 11, 3),
        "election_type": "General",
        "filing_type": "10-Day Report",
        "deadline": date(2026, 10, 24),
        "grace_period_days": 0,
        "notes": "2026 General - 10-day pre-election report deadline",
    },
    {
        "election_date": date(2026, 11, 3),
        "election_type": "General",
        "filing_type": "48-Hour Report",
        "deadline": date(2026, 11, 2),
        "grace_period_days": 0,
        "notes": "2026 General - 48-hour election report deadline",
    },
    {
        "election_date": date(2026, 11, 3),
        "election_type": "General",
        "filing_type": "Post-Election Report",
        "deadline": date(2026, 11, 23),
        "grace_period_days": 0,
        "notes": "2026 General - 20-day post-election report deadline",
    },
]


def seeded_filing_calendar_entries() -> list[FilingCalendarEntry]:
    """Return pre-seeded filing calendar entries from known SOS publication data.

    These entries are based on California Political Reform Act filing
    deadlines and are manually verified against SOS publications.
    """
    entries = []
    for data in KNOWN_FILING_DEADLINES:
        entries.append(FilingCalendarEntry(**data))
    return entries


# ------------------------------------------------------------------ #
#  Database insertion helpers
# ------------------------------------------------------------------ #


def upsert_filing_calendar(
    entries: list[FilingCalendarEntry],
    database_url: str,
    replace: bool = False,
) -> tuple[int, int]:
    """Upsert filing calendar entries into the database.

    Args:
        entries: Filing calendar entries to insert.
        database_url: Postgres connection URL.
        replace: If True, delete existing entries before inserting.

    Returns:
        (inserted_count, updated_count)
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_size=3, max_overflow=5)

    inserted = 0
    updated = 0

    with engine.begin() as conn:
        if replace:
            conn.execute(text("DELETE FROM filing_calendar"))
            logger.info("Replaced all filing_calendar entries")

        for entry in entries:
            d = entry.to_dict()
            result = conn.execute(
                text("""
                    INSERT INTO filing_calendar
                        (election_date, election_type, filing_type, deadline,
                         grace_period_days, extended_deadline, source, notes)
                    VALUES
                        (:election_date, :election_type, :filing_type, :deadline,
                         :grace_period_days, :extended_deadline, :source, :notes)
                    ON CONFLICT DO NOTHING
                    RETURNING 1
                """),
                {
                    "election_date": d["election_date"],
                    "election_type": d["election_type"],
                    "filing_type": d["filing_type"],
                    "deadline": d["deadline"],
                    "grace_period_days": d["grace_period_days"],
                    "extended_deadline": d["extended_deadline"],
                    "source": d["source"],
                    "notes": d["notes"],
                },
            )
            rows = result.fetchall()
            if rows:
                inserted += 1

        # If we have conflicts, update them
        for entry in entries:
            d = entry.to_dict()
            result = conn.execute(
                text("""
                    UPDATE filing_calendar SET
                        election_type = :election_type,
                        filing_type = :filing_type,
                        deadline = :deadline,
                        grace_period_days = :grace_period_days,
                        extended_deadline = :extended_deadline,
                        notes = :notes
                    WHERE election_date = :election_date
                      AND filing_type = :filing_type
                    RETURNING 1
                """),
                {
                    "election_date": d["election_date"],
                    "election_type": d["election_type"],
                    "filing_type": d["filing_type"],
                    "deadline": d["deadline"],
                    "grace_period_days": d["grace_period_days"],
                    "extended_deadline": d["extended_deadline"],
                    "notes": d["notes"],
                },
            )
            rows = result.fetchall()
            if rows:
                updated += 1

    logger.info(
        "Upserted filing_calendar: %d inserted, %d updated",
        inserted,
        updated,
    )
    return inserted, updated


def upsert_election_results(
    entries: list[ElectionResultEntry],
    database_url: str,
    replace: bool = False,
) -> tuple[int, int]:
    """Upsert election results PDF entries into the database.

    Args:
        entries: Election result entries to insert.
        database_url: Postgres connection URL.
        replace: If True, delete existing entries before inserting.

    Returns:
        (inserted_count, updated_count)
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_size=3, max_overflow=5)

    inserted = 0
    updated = 0

    with engine.begin() as conn:
        if replace:
            conn.execute(text("DELETE FROM election_results"))
            logger.info("Replaced all election_results entries")

        for entry in entries:
            d = entry.to_dict()
            result = conn.execute(
                text("""
                    INSERT INTO election_results
                        (election_date, election_type, jurisdiction,
                         sub_jurisdiction, pdf_url, pdf_filename,
                         file_size_bytes, discovered_at, notes)
                    VALUES
                        (:election_date, :election_type, :jurisdiction,
                         :sub_jurisdiction, :pdf_url, :pdf_filename,
                         :file_size_bytes, :discovered_at, :notes)
                    ON CONFLICT DO NOTHING
                    RETURNING 1
                """),
                {
                    "election_date": d["election_date"],
                    "election_type": d["election_type"],
                    "jurisdiction": d["jurisdiction"],
                    "sub_jurisdiction": d["sub_jurisdiction"],
                    "pdf_url": d["pdf_url"],
                    "pdf_filename": d["pdf_filename"],
                    "file_size_bytes": d["file_size_bytes"],
                    "discovered_at": d["discovered_at"],
                    "notes": d["notes"],
                },
            )
            rows = result.fetchall()
            if rows:
                inserted += 1

    logger.info(
        "Upserted election_results: %d inserted, %d updated",
        inserted,
        updated,
    )
    return inserted, updated


# ------------------------------------------------------------------ #
#  CLI helpers
# ------------------------------------------------------------------ #


def format_filing_calendar(entries: list[FilingCalendarEntry]) -> str:
    """Format filing calendar entries for console output."""
    lines = [f"Filing Calendar: {len(entries)} entries\n"]
    lines.append("-" * 80)
    hdr = "  ".join([
        f"{'Election Date':<14}", f"{'Type':<10}",
        f"{'Filing Type':<18}", f"{'Deadline':<12}",
        f"{'Source':<10}",
    ])
    lines.append(hdr)
    lines.append("-" * 80)
    for entry in sorted(entries, key=lambda e: e.election_date):
        lines.append(
            f"{str(entry.election_date):<14} "
            f"{entry.election_type:<12} "
            f"{entry.filing_type or '':<20} "
            f"{str(entry.deadline) if entry.deadline else '':<14} "
            f"{entry.source:<10}"
        )
    lines.append("-" * 80)
    return "\n".join(lines)


def format_election_results(entries: list[ElectionResultEntry]) -> str:
    """Format election results entries for console output."""
    lines = [f"Election Results PDFs: {len(entries)} entries\n"]
    lines.append("-" * 80)
    lines.append(f"{'Election Date':<14} {'Type':<12} {'Jurisdiction':<14} {'PDF URL':<40}")
    lines.append("-" * 80)
    for entry in sorted(entries, key=lambda e: e.election_date):
        url = entry.pdf_url or ""
        url_short = url[-38:] if len(url) > 38 else url
        lines.append(
            f"{str(entry.election_date):<14} "
            f"{entry.election_type:<12} "
            f"{entry.jurisdiction:<14} "
            f"{url_short}"
        )
    lines.append("-" * 80)
    return "\n".join(lines)


# ------------------------------------------------------------------ #
#  Module-level entry point (for debugging)
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m state.scrapers [calendar|results]")
        print("  calendar  — Scrape filing calendar entries")
        print("  results   — Scrape election results PDF links")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "calendar":
        # Scrape from SOS page
        entries = scrape_filing_calendar()
        print(format_filing_calendar(entries))

        # Also print seeded entries
        seeded = seeded_filing_calendar_entries()
        print(f"\nSeeded entries: {len(seeded)}")
        print(format_filing_calendar(seeded))

        # Combine and deduplicate
        all_entries = {
            (e.election_date, e.filing_type): e for e in entries + seeded
        }
        print(f"\nTotal unique entries: {len(all_entries)}")
        print(format_filing_calendar(list(all_entries.values())))

    elif cmd == "results":
        entries = scrape_election_results_pdf_links()
        print(format_election_results(entries))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
