# Ballot Measure & Election Metadata — Data Sources

## Overview

Election metadata in this system comes from two sources:

1. **CAL-ACCESS raw data** (`dbwebexport.zip`) — ballot measure metadata
2. **SOS Elections Division** — election results PDF discovery (no structured data)

---

## 1. BALLOT_MEASURES_CD (from CAL-ACCESS)

**Source:** `CalAccess/DATA/BALLOT_MEASURES_CD.TSV` in the SOS dbwebexport.zip

**Columns:**

| Column | Type | Description |
|--------|------|-------------|
| election_date | DATE | Date of the election |
| filer_id | VARCHAR(20) | Filer ID associated with the measure |
| measure_no | VARCHAR(20) | Measure number (e.g., "1A", "1B", "5") |
| measure_name | TEXT | Full measure name/description |
| measure_short_name | VARCHAR(200) | Short title (nullable) |
| jurisdiction | VARCHAR(60) | Geographic scope (Statewide, County, City) |

**Key:** `(election_date, measure_no)`

**Notes:**
- ~110 rows in historical data (as of Aug 2026)
- Covers statewide, county, and city measures
- Links to RCPT_CD/EXPPD_CD via `filer_id` and `election_date`

**Database Table:** `ballot_measures`

---

## 2. FILING_CALENDAR (manually populated)

**Source:** SOS publications; no downloadable TSV

**Columns:**

| Column | Type | Description |
|--------|------|-------------|
| calendar_id | SERIAL PK | Auto-incrementing |
| election_date | DATE | Election date |
| report_type | VARCHAR(50) | Report type (PRE-QUAL, QUARTERLY, YEAR-END, 48-HOUR, etc.) |
| deadline_date | DATE | Filing deadline |
| grace_period_days | INTEGER | Grace period after deadline |
| source_url | VARCHAR(500) | Reference URL |
| notes | TEXT | Additional context |

**Notes:**
- Populated manually from SOS calendar pages
- Used by `core/workflows/scheduler.py` to warn about upcoming deadlines
- Can be populated programmatically in future via SOS web scraping

**Database Table:** `filing_calendar`

---

## 3. ELECTION_RESULTS (PDF discovery)

**Source:** SOS Elections Division website

**URLs:**
- `https://www.sos.ca.gov/elections/election-data-and-reports/`
- `https://www.sos.ca.gov/elections/election-results/`

**Columns:**

| Column | Type | Description |
|--------|------|-------------|
| election_id | SERIAL PK | Auto-incrementing |
| election_date | DATE | Election date |
| election_type | VARCHAR(30) | General, Primary, Special, Consolidated |
| jurisdiction | VARCHAR(100) | Statewide, County, City |
| sub_jurisdiction | VARCHAR(100) | e.g. "District 35" |
| pdf_url | VARCHAR(500) | Full URL to PDF report |
| pdf_filename | VARCHAR(200) | Local filename after download |
| file_size_bytes | BIGINT | PDF file size |
| discovered_at | TIMESTAMPTZ | Discovery timestamp |
| notes | TEXT | Additional context |

**Notes:**
- SOS publishes election results as PDF reports, not structured data
- This table tracks discovered PDFs for downstream parsing
- Precinct-level results are excluded per project spec
- Future: PDF text extraction to build structured results table

**Database Table:** `election_results`

---

## Cross-References

| Ballot/Measure Field | Links To | Field |
|---------------------|----------|-------|
| `ballot_measures.election_date` | `rcpt_cd.election_date` |
| `ballot_measures.election_date` | `filings.election_date` |
| `ballot_measures.filer_id` | `rcpt_cd.filer_id` |
| `ballot_measures.filer_id` | `filings.filer_id` |
| `rcpt_cd.ballot_issue` | `ballot_measures.measure_no` (string match) |
| `rcpt_cd.jurisdiction` | `ballot_measures.jurisdiction` |

---

## Example Queries

**Total contributions to support/oppose a measure:**
```sql
SELECT SUM(amount) FROM rcpt_cd
JOIN ballot_measures ON rcpt_cd.filer_id = ballot_measures.filer_id
WHERE ballot_measures.measure_no = '1A'
  AND rcpt_cd.election_date = '2024-11-05';
```

**Top donors by measure:**
```sql
SELECT ctrib_naml, SUM(amount) as total
FROM rcpt_cd
WHERE election_date = '2024-11-05'
GROUP BY ctrib_naml
ORDER BY total DESC
LIMIT 10;
```

---

## Known Gaps

|| Gap | Status |
||-----|--------|
|| No structured county results in CAL-ACCESS | PDF discovery only |
|| No precinct-level data per spec | Out of scope |
|| Filing calendar not downloadable | Scrapper + seeded data |
|| Measure short names sometimes NULL | Inherent in source data |

---

## Scraper Workflow (Step 9)

Filing calendar and election results tables are populated via the
`state.scrapers` module, which combines **seeded data** (manually
verified SOS publication deadlines) with **web scraping** of SOS pages.

### Filing Calendar

**CLI tools:**

```bash
# Full scrape + seed + upsert
python -m state.scrape_filing_calendar

# Dry run (preview only)
python -m state.scrape_filing_calendar --dry-run

# Replace existing data
python -m state.scrape_filing_calendar --replace

# Seed-only (no scraping)
python -m state.scrape_filing_calendar --seed-only
```

**Data sources:**
- Seeded: 10 known filing deadlines for 2024–2026 elections (General,
  Primary, 10-Day Report, 48-Hour Report, Post-Election Report)
- Scraped: SOS Campaign & Lobbying pages for current/upcoming elections

**How seeding works:**
`KNOWN_FILING_DEADLINES` in `state/scrapers.py` contains manually
verified deadlines from the California Political Reform Act. These are
loaded first, then scraped entries are merged and deduplicated.

### Election Results

**CLI tools:**

```bash
# Scrape PDF links + download + upsert
python -m state.scrape_election_results

# Dry run
python -m state.scrape_election_results --dry-run

# Custom download directory
python -m state.scrape_election_results --download-dir /path/to/pdfs/
```

**SOS pages scraped:**
- `https://www.sos.ca.gov/elections/`
- `https://www.sos.ca.gov/elections/previous-elections/`
- `https://www.sos.ca.gov/elections/upcoming-elections/`

**PDF discovery:**
Links ending in `.pdf` are matched against result keywords
(e.g., "election result", "official results", "canvass", "precinct").
PDFs are downloaded to `state/cache/election_results/` for downstream
parsing.

### Integration with ETL Pipeline

Both `FILING_CALENDAR` and `ELECTION_RESULTS` are registered in
`TABLE_DEFINITIONS` with `source="scrape"` and included in
`LOAD_ORDER`. During ETL runs, they are flagged as non-TSV tables
and handled separately from the standard TSV loader.

### Table Schema

**`filing_calendar`:**

| Column | Type | Description |
|--------|------|-------------|
| election_date | DATE | Election date |
| election_type | VARCHAR(30) | General, Primary, Special, etc. |
| filing_type | VARCHAR(30) | PRE-Qualification, 10-Day, 48-Hour, etc. |
| deadline | DATE | Filing deadline |
| grace_period_days | INTEGER | Grace period after deadline |
| extended_deadline | DATE | Extended deadline (if any) |
| source | VARCHAR(50) | Source of the data |
| notes | TEXT | Additional context |

**`election_results`:**

| Column | Type | Description |
|--------|------|-------------|
| election_id | SERIAL PK | Auto-incrementing |
| election_date | DATE | Election date |
| election_type | VARCHAR(30) | General, Primary, Special |
| jurisdiction | VARCHAR(100) | Statewide, County, City |
| sub_jurisdiction | VARCHAR(100) | e.g. "District 35" |
| pdf_url | VARCHAR(500) | Full URL to PDF |
| pdf_filename | VARCHAR(200) | Saved filename |
| file_size_bytes | INTEGER | PDF file size |
| discovered_at | TIMESTAMPTZ | Discovery timestamp |
| notes | TEXT | Additional context |
