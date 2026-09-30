# FEC Bulk Data — Table Discovery Notes

**Source:** FEC bulk downloads (`https://www.fec.gov/files/bulk-downloads/<cycle>/`)
**Discovery Date:** 2026-09-21
**Verification:** Live samples downloaded and examined (2026 cycle)

---

## File Formats

All FEC bulk files are **pipe-delimited (`|`), no header row**. Column order is fixed.

### Individual Contributions (`itcont`, 21 fields)

Source: `indivYY.zip` → `itcont`
Delta inserts: `indivYY_insert.zip` → `insert_itcontYYYY.txt`
Delta deletes: `indivYY_delete.zip` → `delete_itcontYYYY.txt` (same 21 fields)

| # | Field | Example | Notes |
|---|---|---|---|
| 1 | committee_id | `C00000422` | Recipient committee |
| 2 | amendment_indicator | `N` | N=new, A=amended, C=corrected |
| 3 | transaction_type_code | `M9` | Receipt type code |
| 4 | other_committee_id | `P` | Conduit/other committee (or `P` for individual) |
| 5 | **transaction_id** | `202609169904219733` | **20-digit unique ID (YYYYMMDD prefix)** — PRIMARY DEDUP KEY |
| 6 | conduit_code | `15` | Conduit code |
| 7 | contributor_type_code | `IND` | IND=individual, ORG=organization, CCM=committee, PAC=PAC, PF=party |
| 8 | contributor_name | `RAFLA-YUAN, ERIC LEI MD` | LAST, FIRST MI format |
| 9 | contributor_city | `SAN DIEGO` | |
| 10 | contributor_state | `CA` | CA lens key |
| 11 | contributor_zip | `921032110` | |
| 12 | contributor_employer | `SELF-EMPLOYED` | |
| 13 | contributor_occupation | `PHYSICIAN` | |
| 14 | contribution_date | `08022026` | MMDDYYYY format |
| 15 | contribution_amount | `500` | May be empty or negative (refunds) |
| 16 | aggregate_year_to_date | | |
| 17 | memo_code | | |
| 18 | memo_reference | `AE4186ED31CEC42038B3` | |
| 19 | election_code | | |
| 20 | committee_fec_id | `2012199` | |
| 21 | receipt_no | `4091620261598597236` | FEC receipt number |

### Committee Master (`cm.txt`, 15 fields)

Source: `cmYY.zip` → `cm.txt`
Row count (2026): ~20,724 committees

| # | Field | Example |
|---|---|---|
| 1 | committee_id | `C00000059` |
| 2 | committee_name | `HALLMARK CARDS, INC. PAC (HALLPAC)` |
| 3 | treasurer_name | `KLEIN, CASSIE MS.` |
| 4 | street_1 | `2501 MCGEE, MD853` |
| 5 | street_2 | |
| 6 | city | `KANSAS CITY` |
| 7 | state | `MO` |
| 8 | zip | `64108` |
| 9 | organization_type | `B` |
| 10 | committee_type | `Q` |
| 11 | type_designation | `UNK` |
| 12 | connected_organization_name | `M` |
| 13 | affiliation | `C` |
| 14 | fec_election_year | `HALLMARK CARDS, INC.` |
| 15 | (trailing) | |

### Candidate Master (`cn.txt`, 15 fields)

Source: `cnYY.zip` → `cn.txt`
Row count (2026): ~8,620 candidates

| # | Field | Example |
|---|---|---|
| 1 | candidate_id | `H0AL01055` |
| 2 | candidate_name | `CARL, JERRY LEE, JR` |
| 3 | party | `REP` |
| 4 | cycle | `2026` |
| 5 | office | `AL` |
| 6 | state | `H` |
| 7 | district | `01` |
| 8-15 | (remaining fields) | |

### Candidate-Committee Linkage (`ccl.txt`, 7 fields)

Source: `cclYY.zip` → `ccl.txt`
Row count (2026): ~8,108 linkages

| # | Field | Example |
|---|---|---|
| 1 | committee_id | `C00778159` |
| 2 | start_year | `2022` |
| 3 | end_year | `2026` |
| 4 | cand_id | `C00778159` |
| 5 | cand_office | `Q` |
| 6 | cand_state | `D` |
| 7 | cand_party | `260464` |

---

## Key Findings

1. **transaction_id is the dedup key:** 20-digit unique identifier starting with YYYYMMDD. Not receipt_no.
2. **Delete deltas contain full rows:** Same 21-field format as inserts, not just keys.
3. **Date format:** MMDDYYYY (e.g., `08022026` = August 2, 2026)
4. **Name format:** LAST, FIRST MI (same convention as CAL-ACCESS)
5. **Contributor type codes:** IND, ORG, CCM, PAC, PF
6. **Amendment indicators:** N (new), A (amended), C (corrected)

## Volume Estimates

| File | 2024 Size | 2026 Size | Rows (est.) |
|---|---|---|---|
| indivYY.zip | 4.24 GB | 2.20 GB | 50-100M+ |
| othYY.zip | 505 MB | 213 MB | 5-10M |
| pas2YY.zip | 24.7 MB | 8.2 MB | 100-500K |
| cmYY.zip | 868 KB | 868 KB | ~20K |
| cnYY.zip | 308 KB | 308 KB | ~8.5K |
| oppexpYY.zip | 63 MB | 45 MB | 1-5M |

## CA Data Presence

Verified CA data in 2026 insert delta samples:
- CA contributors: `RAFLA-YUAN, ERIC LEI MD` (San Diego, CA)
- CA organizations: `SAN MANUEL BAND OF MISSION INDIANS` (Los Angeles, CA)
- CA-targeted IEs: Confirmed in `oppexp` file
