# Ballot Measure & Election Metadata - Data Sources

## 1. CAL-ACCESS Ballot Measures (BALLOT_MEASURES_CD.TSV)

**Source:** Included in the main CAL-ACCESS raw data dump at `https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip`

| Field | Type | Description |
|-------|------|-------------|
| ELECTION_DATE | DATE | Date of the election |
| FILER_ID | VARCHAR(20) | Filer ID associated with the measure |
| MEASURE_NO | VARCHAR(20) | Measure number (e.g., 1A, 1B, 5) |
| MEASURE_NAME | TEXT | Full measure name/description |
| MEASURE_SHORT_NAME | VARCHAR(200) | Short title (nullable) |
| JURISDICTION | VARCHAR(60) | Geographic scope (Statewide, County, City) |

**Characteristics:**
- ~110 rows in historical data
- Covers all CA statewide, county, and city ballot measures
- Links to contribution/expenditure data via ELECTION_DATE and MEASURE_NO fields

**Database Table:** `ballot_measures`

## 2. Filing Calendar

**Source:** Populated manually from SOS publications and cross-referenced with report types.

| Field | Type | Description |
|-------|------|-------------|
| CALENDAR_ID | SERIAL PK | Auto-incrementing ID |
| ELECTION_DATE | DATE | Election date |
| REPORT_TYPE | VARCHAR(50) | Report type (PRE-QUALIFICATION, QUARTERLY, YEAR-END, 48-HOUR, TERMINAL) |
| DEADLINE_DATE | DATE | Filing deadline |
| GRACE_PERIOD_DAYS | INTEGER | Grace period after deadline (if any) |
| SOURCE_URL | VARCHAR(500) | Reference URL |
| NOTES | TEXT | Additional context |

**Use:** The daily scheduler (`core/workflows/scheduler.py`) queries this table to determine if any filing deadlines fall within the next 7 days and logs warnings for upcoming deadlines.

**Database Table:** `filing_calendar`

## 3. SOS Elections Division - Election Results (PDF)

**Source:** SOS Elections Division website at `https://www.sos.ca.gov/elections/election-data-and-reports/`

The SOS publishes election results as PDF reports, not as structured data files. This table tracks discovered PDFs for downstream parsing.

| Field | Type | Description |
|-------|------|-------------|
| ELECTION_ID | SERIAL PK | Auto-incrementing ID |
| ELECTION_DATE | DATE | Date of the election |
| ELECTION_TYPE | VARCHAR(30) | General, Primary, Special, Consolidated |
| JURISDICTION | VARCHAR(100) | Statewide, County, City |
| SUB_JURISDICTION | VARCHAR(100) | e.g., "District 35", "Los Angeles County" |
| PDF_URL | VARCHAR(500) | Full URL to the PDF report |
| PDF_FILENAME | VARCHAR(200) | Local filename after download |
| FILE_SIZE_BYTES | BIGINT | Size of the PDF |
| DISCOVERED_AT | TIMESTAMPTZ | When the PDF was discovered |
| NOTES | TEXT | Additional context |

**Discovery Process:**
1. Crawl the SOS Elections Division results page periodically
2. Parse PDF links and extract election metadata from filenames/URLs
3. Populate the `election_results` table
4. Future work: OCR/PDF text extraction to build a structured election results table (excludes precinct-level data per spec)

**Database Table:** `election_results`

## 4. Cross-References

The ballot measures data links to the financial disclosure tables through these fields:

- **`ballot_measures.election_date`** → `rcpt_cd.election_date`, `filings.election_date`
- **`ballot_measures.filer_id`** → `rcpt_cd.filer_id`, `filings.filer_id`
- **`rcpt_cd.ballot_issue`** → `ballot_measures.measure_no` (via string match)
- **`rcpt_cd.jurisdiction`** → `ballot_measures.jurisdiction`

These cross-references enable queries like:
- "Total contributions to support/oppose Measure XX by election date"
- "Top 10 donors to ballot measure campaigns in 2024"
- "Outside spending by election type"

## 5. Known Gaps

| Gap | Status |
|-----|--------|
| No structured county-level results in CAL-ACCESS | Covered by PDF discovery only |
| No precinct-level results per spec | Excluded from scope |
| Filing calendar not available as TSV | Requires manual population |
| Ballot measure short descriptions occasionally missing | NULL in data source |
