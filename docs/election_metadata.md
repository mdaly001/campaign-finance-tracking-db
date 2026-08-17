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

| Gap | Status |
|-----|--------|
| No structured county results in CAL-ACCESS | PDF discovery only |
| No precinct-level data per spec | Out of scope |
| Filing calendar not downloadable | Manual population required |
| Measure short names sometimes NULL | Inherent in source data |
