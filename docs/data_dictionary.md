# Campaign Finance Database — Data Dictionary

## Schema Overview

**Database:** Campaign Finance Disclosure Database — Phase 1 (CA State)
**Source:** California Secretary of State, CAL-ACCESS Raw Data
**Schema Version:** v1
**Total Tables:** 77 (including partition stubs)
**Partitioned Tables:** 3 (rcpt_cd, exppd_cd, loans_cd)
**Views:** 4 (v_candidate_contributions, v_committee_summary, v_top_contributors, v_lobbying_activity)

---

## Table Index

### Reference / Dimension Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `lookup_codes` | CAL-ACCESS code definitions | 5,000 | code_type, code_id, code_desc |
| `acronyms` | Committee acronym definitions | 500 | acronym, stands_for |
| `filer_types` | Filer type definitions | 30 | filer_type, description |
| `filer_status_types` | Active, Inactive, Cancelled | 5 | status_type, status_desc |
| `group_types` | Committee group types | 20 | grp_id, grp_name |
| `report_types` | Report type definitions | 40 | rpt_id, rpt_name |
| `legislative_sessions` | CA legislative sessions | 20 | session_id, begin_date |
| `filing_periods` | Filing period definitions | 200 | period_id, start_date |
| `filing_types` | Cover sheet filing types | 20 | filing_type |

### Entity / Committee Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `filers` | Master filer/committee registry | 150,000 | filer_id, source |
| `filername` | Filer names (many per filer) | 300,000 | xref_filer_id, naml |
| `address_master` | Address master | 200,000 | adrid, city, st, zip4 |
| `filer_address` | Filer ↔ Address mapping | 250,000 | filer_id, adrid |
| `filer_xref` | Filer ID cross-references | 50,000 | filer_id, xref_id |
| `filer_links` | Filer relationships | 30,000 | filer_id_a, filer_id_b |
| `filer_type_assignments` | Filer-to-type assignments | 150,000 | filer_id, filer_type |
| `filer_ethics_class` | Ethics class for lobbying | 5,000 | filer_id, session_id |
| `filer_interests` | Lobbying interest codes | 10,000 | filer_id, interest_cd |
| `filer_acronyms` | Filer acronyms | 10,000 | acronym, filer_id |
| `names_master` | Entity name master | 500,000 | namid, naml, fullname |

### Filing / Report Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `filings` | Cover sheets / filings | 500,000 | filing_id, filing_type, filing_date |
| `efs_filing_log` | E-filing submission log | 500,000 | filer_id, filing_date |
| `received_filings` | SOS receipt tracking | 500,000 | filer_id, filing_id |
| `hdr` | Filing header records | 500,000 | filing_id, rec_type |
| `header_defs` | Form header definitions | 500 | form_id, line_number |
| `image_links` | Document image links | 100,000 | img_link_id, img_id |

### Core Fact Tables (PARTITIONED)

| Table | Description | Rows (est.) | Key Columns | Partitions |
|-------|-------------|-------------|-------------|------------|
| `rcpt_cd` | **Receipts** — Contributions, Expenditures, Refunds | ~50M | filing_id, filer_id, amount, receipt_dt | 10 (y2018–y2027) |
| `cntrb_cd` | Contributors master | 2,000,000 | ctrib_id, ctrib_naml, total_gives | — |
| `exppd_cd` | Expenditures | ~15M | filing_id, filer_id, amount, expn_date | 10 (y2018–y2027) |
| `loans_cd` | Loans received/made | ~2M | filing_id, cmte_id, loan_amt, loan_dt | 10 (y2018–y2027) |
| `inttrf_cd` | Inter-committee transfers | ~500K | tran_id, amount, tran_dt | — |
| `debt_cd` | Debts owed | ~200K | filing_id, end_bal | — |

### Supporting Fact Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `smry_cd` | Filing summary totals | 500,000 | filing_id, amount_a–c |
| `splts_cd` | Split records | 300,000 | filing_id, ptran_id |
| `text_memo` | Text memo descriptions | 1,000,000 | filing_id, text4000 |

### Disclosure Reports (CVR)

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `cvr_campaign_disclosure` | F496 Campaign Disclosure | 500,000 | filing_id, total_contributions |
| `cvr_registration` | F400 Committee Registration | 150,000 | filing_id, filer_id |
| `cvr_so` | F460 Statement of Organization | 150,000 | filing_id, filer_id |
| `cvr_lobby_disclosure` | F455 Lobbying Disclosure | 20,000 | filing_id, lby_reg_id |
| `cvr2_campaign_disclosure` | Compact campaign disclosure | 2,000,000 | cmte_id, item_amt |
| `cvr2_lobby_disclosure` | Compact lobbying disclosure | 50,000 | filing_id, tran_id |
| `cvr3_verification_info` | E-filing verification | 500,000 | filing_id, sig_date |
| `cvr_e530` | E-530 Political Candidate Statements | 10,000 | cand_id, cash_on_hand |
| `cvr_f470` | F-470 Contribution/Expenditure Schedule | 5,000 | filer_id, rpt_date |

### Schedule Tables (Form-Specific)

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `s401_cd` | Schedule S401 — Independent Expenditures | ~3M | filer_id, expn_amt, expn_date |
| `s496_cd` | Schedule S496 — expenditure lines (no payee name fields in this export) | 75,636 | filing_id, amount, exp_date, expn_dscr |
| `s497_cd` | Form 497 — Large Contribution report (24-hour reporting; donor in `enty_*` fields) | 1,392,112 | filing_id, ctrib_date, amount, enty_naml, enty_namf, cmte_id |
| `s498_cd` | Form 498 — Rapid Disclosure (F498-R receipts + F498-A attribution lines) | 27,520 | filing_id, date_rcvd, amt_rcvd, amt_attrib, payor_naml, payor_namf |
| `f495p2` | F-495 Part 2 — Candidate Contributions | 20,000 | elect_date, contribamt |
| `f501_502` | F-501/F-502 Report of Organization/Candidate | 150,000 | filer_id, cand_office |
| `f690p2` | F-690 Part 2 — Lobbying Amendments | 5,000 | filing_id, exec_date |

### Expenditure & Payment Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `latt_cd` | Late-Attest Payments | ~100K | filer_id, pmt_amt, pmt_date |
| `lpay_cd` | Loan Payments | ~500K | filer_id, loan_id, repmt_dt |
| `lccm_cd` | Campaign Committee Memo Payments | ~200K | filer_id, recip_id, pmt_amt |
| `lexp_cd` | Lobbying Expenditures | ~100K | filer_id, expn_amt, expn_date |
| `loth_cd` | Lobbyist Other Transactions | ~50K | filer_id, amount, actv_dt |

### Lobbying Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `lemp_cd` | Lobbyist Employment/Activities | 10,000 | filer_id, lby_reg_id, activities_desc |
| `lobby_amendments` | Lobbying amendment log | 5,000 | filing_id, exec_date |
| `lobbying_chg_log` | Lobbying change history | 50,000 | filer_id, change_no, log_dt |
| `lobbyist_contributions` | Lobbyist contributions (all periods) | 200,000 | filer_id, contribution_dt, amount |
| `lobbyist_employers` | Lobbyist employer records | 10,000 | employer_id, session_id |
| `lobbyist_employer_firms` | Lobbyist employer-firm relationships | 10,000 | employer_id, firm_id |
| `lobbyist_employer_lobbyist` | Lobbyist-employer relationships | 10,000 | lobbyist_id, employer_id |
| `lobbyist_firms` | Lobbyist firm records | 5,000 | firm_id, total_amt |
| `lobbyist_firm_employer` | Lobbyist firm-employer relationships | 10,000 | firm_id, filing_id |
| `lobbyist_firm_lobbyist` | Lobbyist-firm relationships | 10,000 | lobbyist_id, firm_id |

### Ballot Measure Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `ballot_measures` | Ballot measure metadata | 5,000 | election_date, measure_no, measure_name |

### Filing Calendar

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `filing_calendar` | Election dates and filing deadlines | 2,000 | election_date, filing_type, deadline |

### ballot_measures
- **Category:** Ballot Measures
- **Source:** BALLOT_MEASURES_CD.TSV (CAL-ACCESS raw data)
- **Partitioned:** No
- **Key:** (election_date, measure_no)
- **Columns:**
  - `election_date` DATE NOT NULL — Date of the election
  - `filer_id` VARCHAR(20) NOT NULL — Filer ID associated with the measure
  - `measure_no` VARCHAR(20) NOT NULL — Measure number (e.g., "1A", "5")
  - `measure_name` TEXT NOT NULL — Full measure name/description
  - `measure_short_name` VARCHAR(200) — Short title (nullable)
  - `jurisdiction` VARCHAR(60) NOT NULL — Geographic scope
- **Indexes:** idx_ballot_msr_election, idx_ballot_msr_jurisdiction, idx_ballot_msr_filer
- **Notes:** ~110 rows in historical data. Links to rcpt_cd/filings via filer_id + election_date

## filing_calendar
- **Category:** Election Metadata
- **Source:** Manual population from SOS publications (no TSV)
- **Partitioned:** No
- **Columns:**
  - `calendar_id` SERIAL PK — Auto-incrementing ID
  - `election_date` DATE NOT NULL — Election date
  - `report_type` VARCHAR(50) NOT NULL — Report type
  - `deadline_date` DATE NOT NULL — Filing deadline date
  - `grace_period_days` INTEGER DEFAULT 0 — Grace period after deadline
  - `source_url` VARCHAR(500) — Reference URL
  - `notes` TEXT — Additional context
- **Indexes:** idx_filing_cal_election, idx_filing_cal_report
- **Notes:** Used by scheduler.py to warn about upcoming filing deadlines

## election_results
- **Category:** Election Metadata
- **Source:** SOS Elections Division PDF discovery
- **Partitioned:** No
- **Columns:**
  - `election_id` SERIAL PK — Auto-incrementing ID
  - `election_date` DATE NOT NULL — Date of the election
  - `election_type` VARCHAR(30) NOT NULL — General, Primary, Special, Consolidated
  - `jurisdiction` VARCHAR(100) NOT NULL — Statewide, County, City
  - `sub_jurisdiction` VARCHAR(100) — Sub-jurisdiction detail
  - `pdf_url` VARCHAR(500) — Full URL to the PDF report
  - `pdf_filename` VARCHAR(200) — Local filename after download
  - `file_size_bytes` BIGINT — Size of the PDF
  - `discovered_at` TIMESTAMPTZ DEFAULT NOW() — Discovery timestamp
  - `notes` TEXT — Additional context
- **Indexes:** idx_election_results_date, idx_election_results_type, idx_election_results_jurisdiction
- **Notes:** Tracks discovered PDF election result files for downstream parsing. Precinct-level results excluded per spec.

## Entity Resolution Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `entity` | Resolved entity master | 1,000,000 | entity_id, naml, entity_type |
| `entity_alias` | Entity aliases for fuzzy matching | 5,000,000 | entity_id, alias_name |
| `entity_merge_queue` | Pending entity merges | 0 | queue_id, entity_a_id, entity_b_id |

### ETL Infrastructure Tables

| Table | Description | Rows (est.) | Key Columns |
|-------|-------------|-------------|-------------|
| `source_info` | Data source metadata | 1,000 | source_id, zip_checksum, load_date |
| `load_checkpoint` | ETL load checkpoints | 10,000 | table_name, file_hash, processed_date |
| `etl_dead_letter` | Bad row quarantine | 0 | dead_letter_id, table_name, row_data |

---

## Partition Information

### rcpt_cd (Receipts) — PARTITIONED BY receipt_dt

| Partition | Range | Rows (est.) |
|-----------|-------|-------------|
| rcpt_cd_y2018 | 2018-01-01 to 2019-01-01 | 5,000,000 |
| rcpt_cd_y2019 | 2019-01-01 to 2020-01-01 | 5,000,000 |
| rcpt_cd_y2020 | 2020-01-01 to 2021-01-01 | 5,000,000 |
| rcpt_cd_y2021 | 2021-01-01 to 2022-01-01 | 5,000,000 |
| rcpt_cd_y2022 | 2022-01-01 to 2023-01-01 | 5,000,000 |
| rcpt_cd_y2023 | 2023-01-01 to 2024-01-01 | 5,000,000 |
| rcpt_cd_y2024 | 2024-01-01 to 2025-01-01 | 5,000,000 |
| rcpt_cd_y2025 | 2025-01-01 to 2026-01-01 | 5,000,000 |
| rcpt_cd_y2026 | 2026-01-01 to 2027-01-01 | 5,000,000 |
| rcpt_cd_y2027 | 2027-01-01 to 2028-01-01 | 5,000,000 |

### exppd_cd (Expenditures) — PARTITIONED BY expn_date

Same 10-year partition scheme as rcpt_cd. Estimated 1.5M rows/year.

### loans_cd (Loans) — PARTITIONED BY loan_dt

Same 10-year partition scheme as rcpt_cd. Estimated 200K rows/year.

---

## Views

### v_candidate_contributions

Total contributions aggregated by candidate name, office, and election.

**Key columns:**
- candidate_name: Concatenated cand_naml + cand_namf
- contribution_count: COUNT(*)
- total_amount: SUM(amount)
- min_amount, max_amount: Range of contribution amounts

### v_committee_summary

Latest filing summary per committee.

**Key columns:**
- committee_name: NAML from filername (latest effect_dt)
- total_contributions, total_expenditures: From cvr_campaign_disclosure
- net_position: Derived cash position

### v_top_contributors

Top 1,000 contributors by lifetime giving.

**Key columns:**
- contributor_name: Concatenated ctrib_naml + ctrib_namf
- total_gives: From cntrb_cd
- committees_contributed_to: COUNT(DISTINCT committee_id from rcpt_cd)

### v_lobbying_activity

Lobbying activity summary per registered lobbyist.

**Key columns:**
- lby_reg_id, lby_reg_name
- filings_count: Number of associated filings
- activity_descriptions: Count of non-null activity descriptions

### receipts_all

Contribution source-of-truth for the MCP contribution tools. A simple
`UNION ALL` of the periodic receipt line table and the 24-hour
contribution forms, normalized to one column set.

- Sources: `rcpt_cd` (periodic), `s497_cd` (24-hour large contributions),
  `s498_cd` (24-hour receipts, `form_type = 'F498-R'` only)
- Columns: `src` (origin table), `filing_id`, `amend_id`, `tran_id`,
  `receipt_date`, `amount`, `donor_naml`/`donor_namf` (individual) or
  `ctrib_dscr` (organization), `cmte_id` (donor committee, if any),
  `memo_refno`, `donor_key`, `donor_name`
- **No de-duplication in the view**: a gift reported in both a 24-hour
  report and the later periodic re-report appears twice here. The MCP
  tools apply cross-source de-duplication on top (filer, donor key,
  amount, near-date), so such a gift counts once in tool results.
  Ad-hoc SQL against this view must de-duplicate itself.

---

## Column Type Conventions

- **Monetary values:** `NUMERIC(15,2)` — no FLOAT/REAL
- **Dates:** `DATE` or `TIMESTAMPTZ` (UTC)
- **Text:** `VARCHAR(n)` with appropriate limits
- **Integers:** `INTEGER` or `SMALLINT`
- **Booleans:** `BOOLEAN`
- **Foreign keys:** `VARCHAR(n)` matching referenced PK type
- **Auto-increment:** `SERIAL` or `BIGSERIAL`

## Foreign Key Relationships

```
filers.filer_id ← filername.xref_filer_id / filer_id
filers.filer_id ← filings.filer_id
filers.filer_id ← cvr_campaign_disclosure.filer_id
filers.filer_id ← cvr_registration.filer_id
filers.filer_id ← cvr_so.filer_id
filers.filer_id ← cvr_lobby_disclosure.filer_id
filers.filer_id ← s401_cd.filer_id
filers.filer_id ← filer_filings_cd.filer_id  (rapid tables below join via filing_id)
filings.filing_id ← s497_cd.filing_id
filings.filing_id ← s498_cd.filing_id
filings.filing_id ← s496_cd.filing_id

filings.filing_id ← rcpt_cd.filing_id
filings.filing_id ← exppd_cd.filing_id
filings.filing_id ← loans_cd.filing_id
filings.filing_id ← smry_cd.filing_id
filings.filing_id ← text_memo.filing_id
filings.filing_id ← cvr_campaign_disclosure.filing_id

rcpt_cd.ctrib_id ← cntrb_cd.ctrib_id (via ETL join)

filer_id (from filer_types) → filer_type_assignments.filer_type
legislative_sessions.session_id → filer_type_assignments.session_id
legislative_sessions.session_id → filer_interests.session_id
filing_periods.period_id → filings.period_id
```

## Index Strategy

- **Primary indexes:** All PRIMARY KEY columns (B-tree)
- **FK indexes:** All foreign key columns
- **Query-optimized indexes:**
  - `rcpt_cd`: cmte_id, ctrib_naml, filing_id, rcpt_date
  - `exppd_cd`: (filer_id, expn_date), amount DESC, payee_naml
  - `s401_cd`: (filer_id, expn_date), expn_amt DESC
  - `s497_cd`: filing_id (supports the `receipts_all` union view)
  - `s498_cd`: filing_id (supports the `receipts_all` union view)
- **Entity resolution:** GIN indexes on naml/fullname using `to_tsvector('simple')`
- **Fuzzy matching:** pg_trgm and fuzzystrmatch extensions enabled
