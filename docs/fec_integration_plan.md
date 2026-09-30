# FEC Data Integration Plan — Phase 2 (Federal, California-focused)

**Status:** Approved — all decisions resolved (2026-09-21)
**Scope:** Ingest FEC (federal) campaign-finance disclosure data into CFDB with a California lens,
reusing the existing `core/etl` adapter/loader/checkpoint framework and the MCP query surface.

All source URLs, file formats, and sizes in this document were **live-verified on 2026-09-21**
against `fec.gov` / FEC's S3 bulk bucket and the openFEC API models.

---

## 1. Goals

1. Load FEC federal disclosure data (committees, candidates, contributions, independent
   expenditures) into the same Postgres database as CAL-ACCESS.
2. Make **California** the organizing lens:
   - **CA federal candidates** — US Senate / House candidates and their principal committees (candidate office state = CA, all 52 districts).
   - **CA-based committees** — PACs / party committees with a CA mailing address (regardless of whom they fund).
   - **CA money flows** — contributions *from* CA-resident donors to any federal committee, and independent expenditures *targeting* CA races (from any-state spenders).
3. Cross-reference federal ↔ CAL-ACCESS entities (same donors, vendors, and organizations appearing in both jurisdictions) using the existing entity-resolution machinery.
4. Expose the federal data through the MCP server without destabilizing the pinned CA tool contracts.

**Non-goals (for now):** full national history back to 1993 (add later as disk allows), FEC audit/enforcement data, Form 13 (donor coordination), lobbying (LDA is a different agency).

---

## 2. What FEC actually offers (verified)

### 2.1 Bulk downloads — primary ingestion path

Base URL: `https://www.fec.gov/files/bulk-downloads/<cycle>/…` (302-redirects to FEC's
S3 bucket in `us-gov-west-1`; **use the fec.gov URL**, not the S3 URL directly).
Files are organized by 2-year election cycle (`2024`, `2026`, …). Verified inventory per cycle:

| File | Contents | Verified size (2024 / 2026) | Priority |
|---|---|---|---|
| `indivYY.zip` → `itcont` | Individual contributions (Form 3X/3/3P **Schedule A**) | 4.24 GB / 2.20 GB compressed | **P0** |
| `indivYY_insert.zip` | Daily **insert deltas** for Schedule A | ~9–14 MB/day | **P0** (incremental) |
| `indivYY_delete.zip` | Daily **delete deltas** (incl. redactions) | ~9 MB | **P0** (incremental) |
| `othYY.zip` → `itoth` | Non-individual contributions (**Schedule B**: PACs, parties, orgs) | 505 MB / 213 MB | P1 |
| `pas2YY.zip` → `itpas2` | Conduit / earmark contributions (**Schedule C**) | 24.7 MB / 8.2 MB | P1 |
| `cmYY.zip` → `cm` | Committee master (all committees, all cycles) | 868 KB (~20.7K rows) | **P0** |
| `cnYY.zip` → `cn` | Candidate master | 308 KB (~8.6K rows) | **P0** |
| `cclYY.zip` → `ccl` | Candidate ↔ leadership-PAC linkage | ~90 KB | P1 |
| `oppexpYY.zip` → `oppexp` | Independent expenditures & opposition research (**Schedule E** style) | 63 MB / 45 MB | **P0** for CA lens |
| `weballYY.zip`, `webkYY.zip`, `weblYY.zip` | FEC **Online** (web-filed) forms — overlap with paper-filed data | < 0.5 MB each | P2 (dedup study) |
| `candidate_summary_YY.csv`, `committee_summary_YY.csv` | Pre-aggregated totals | ~2–8 MB | P2 (golden numbers) |
| `independent_expenditure_YY.csv` | IE CSV (comma-delimited, has header) | 8.2 MB / 4.8 MB | P2 (cross-check) |
| `Form1Filer_YY.csv`, `Form2Filer_YY.csv` | Committee registration (F1) / candidacy (F2) | ~1–2 MB | P2 |

### 2.2 File formats (verified from live samples, 2026-09-21)

All files are **pipe-delimited (`|`), no header row**. Column order is fixed by the FEC file layout.

**Committee master (`cm.txt`): 15 fields**
```
committee_id|committee_name|treasurer_name|street_1|street_2|city|state|zip|org_type|committee_type|type_designation|connected_org_name|affiliation|...
C00000059|HALLMARK CARDS, INC. PAC (HALLPAC)|KLEIN, CASSIE MS.|2501 MCGEE, MD853||KANSAS CITY|MO|64108|B|Q|UNK|M|C|HALLMARK CARDS, INC.|
```

**Candidate master (`cn.txt`): 15 fields**
```
candidate_id|candidate_name|party|cycle|office|state|district|...
H0AL01055|CARL, JERRY LEE, JR|REP|2026|AL|H|01|O|C|C00697789|PO BOX 852138||MOBILE|AL|36685
```

**Individual contributions (`itcont`): 21 fields**
```
committee_id|amend_ind|trans_type|other_id|transaction_id|conduit_code|contrib_type|contrib_name|contrib_city|contrib_state|contrib_zip|contrib_employer|contrib_occupation|contrib_date|contrib_amount|aggr_ytd|memo_code|memo_ref|elect_cd|cmte_fec_id|receipt_no
C00000422|N|M9|P|202609169904219733|15|IND|RAFLA-YUAN, ERIC LEI MD|SAN DIEGO|CA|921032110|SELF-EMPLOYED|PHYSICIAN|08022026|500||AE4186ED31CEC42038B3|2012199|||4091620261598597236
```

**Candidate-committee linkage (`ccl.txt`): 7 fields**
```
committee_id|start_year|end_year|cand_id|cand_office|cand_state|cand_party
C00778159|2022|2026|C00778159|Q|D|260464
```

**Key findings:**
- **transaction_id** (field 5, 20 digits starting with YYYYMMDD) is the unique identifier for individual contributions — use this as the dedup key, not receipt_no
- **amend_ind** (field 2): N=new, A=amended, C=corrected
- **contrib_date** (field 14): MMDDYYYY format
- **contrib_amount** (field 15): numeric (may be empty or negative for refunds)
- Delete deltas (`delete_itcontYYYY.txt`) contain **full 21-field rows** (not just keys) — match on transaction_id to identify which row to tombstone

### 2.3 OpenFEC REST API — validation & spot-checks only

- Base: `https://api.open.fec.gov/` — **requires a free `api.data.gov` key** (verified:
  keyless requests are rejected with `API_KEY_MISSING`). Sign up: <https://api.data.gov/signup/>.
- Useful endpoints for CA:
  - `/v1/schedules/schedule_a/?state=CA&two_year_transaction_period=2024&api_key=…`
  - `/v1/schedules/schedule_e/?state=CA&…` (IEs targeting CA)
  - `/v1/committees/?state=CA&committee_type=…`
  - `/v1/candidates/?state=CA`
  - `/v1/elections/?state=CA&office=H&year=2024`
- Canonical field names: mirror openFEC's own models
  (`contbr_nm`, `contbr_st`, `contb_receipt_dt`, `contb_receipt_amt`, `two_year_transaction_period`,
  `action_cd` = amendment indicator, `cand_office_st`, …). We adopt these names in our DDL so
  API cross-checks are 1:1.
- **Do not bulk-load via the API** (rate limits; the bulk files are the same data).

### 2.4 Reference docs

- openFEC repo (canonical layouts & code tables): <https://github.com/fecgov/openFEC>
  (`data/efile_guide_f3.json`, `efile_guide_f3p.json`, `efile_guide_f3x.json`, `data/crosswalks/`,
  `webservices/common/models/itemized.py`)
- FEC browse UI for golden-number cross-checks: <https://www.fec.gov/data/> (state-filtered raising/spending views)

---

## 3. How FEC differs from CAL-ACCESS (the gotchas table)

| Concern | CAL-ACCESS (today) | FEC | Integration consequence |
|---|---|---|---|
| File format | TSV, upper-case headers | Pipe-delimited TXT, **no headers**; some CSV-with-header | Generalize `TSVReader` with a `delimiter` param (default `\t`) — small, backward-compatible |
| Date format | `M/D/YYYY [h:mm AM/PM]` | `MMDDYYYY` in itcont; ISO-ish elsewhere | Add `%m%d%Y` to loader `_DT_FORMATS` |
| Cycle | Derived from transaction date | First-class `two_year_transaction_period`; files are per-cycle | Cycle becomes a load parameter + partition key |
| Amendments | `amend_id` per row; dedup views keep max | `amendment_indicator` (`N`=new, `A`=amended, `C`=corrected) + **delete deltas** | New dedup rule: latest version per `transaction_id`; apply delete deltas as tombstones |
| Deletes | None (snapshot) | `indivYY_delete.zip` daily (redactions, reclassifications) — **full rows, not just keys** | Must apply deletes or totals drift upward — **new concept for this codebase** |
| Redaction | Redacted/unredacted export split | 2023 rule: threatened donors can be redacted (name/address blanked via amendment/delete) | Caveat doc + `redacted` flag column |
| Names | Split fields (`naml`/`namf`) | Single `contrib_nm` ("LAST, FIRST MI") + API-side split fields | Store raw single field + derived first/last for matching |
| Committee IDs | Numeric filer/cmte ids, xref tables | `C0xxxxxxx` committee ids, `Hxx/Sxx/Pxx` candidate ids — stable | No xref churn like CA's stale-`cmte_id` problem; still join via ids, not names |
| Money | NUMERIC(p,s) | NUMERIC(30,2) in FEC's own DB | Use `NUMERIC(30,2)` |
| Volume | ~1.5 GB zip, ~10 GB data | ~4.5–7 GB compressed per cycle pair (indiv+oth+oppexp) | **Disk budget ~100–200 GB for 2 cycles national** (revised estimate based on actual data sizes) |
| Filing channels | Single channel | Paper/electronic filers **and** FEC Online filers (web files overlap) | P2: study `weball/webk/webl` overlap before loading; dedup by transaction_id |
| Dedup key | amend_id per row | **transaction_id** (20 digits, unique globally) — not receipt_no | Use transaction_id for upsert conflict resolution |

---

## 4. Architecture decisions (recommended)

### D1. Separate Postgres schema `fec` in the same database — **approved**

```
public   → CAL-ACCESS tables + views (untouched)
fec      → federal tables + views (new)
unredacted → unchanged
```

Why: keeps CA tables/views/tests untouched; `cfdb_reader` grants are explicit per schema;
cross-schema joins (`public.rcpt_cd_deduped` ⋈ `fec.fec_individual_contributions`) enable
cross-jurisdiction queries without a federated layer. Alternative (prefix `fec_` in public)
works but pollutes the CA namespace and weakens the grant boundary.

### D2. Load the **full national** fact rows for the chosen cycles; CA is a view, not a filter — **approved**

CA-centric analysis breaks if we filter at load time:
- CA-targeted IEs are frequently funded by **out-of-state** committees.
- CA donors fund federal races nationwide; "CA money in federal elections" needs the national rows.
- The `row_filter` hook in `LoadConfig` exists if we later want a slim CA-only mode, but the
  default should be national + `v_ca_*` views.

**Revised disk budget (2 cycles, national): ~100–200 GB including indexes.** If that's too much, the
fallback is CA-filtered load (`contrib_state='CA'` ∪ CA-based committees ∪ CA-targeted IEs)
at ~10–20 GB.

### D3. Parallel `federal/` pipeline mirroring `state/` — **approved**

Do **not** refactor `state/etl.py` into a generic runner (its tests pin current behavior).
Instead build the mirror-image pattern the repo already anticipates (`SourceAdapter` docstring
already names "federal"):

```
federal/
  adapter.py     FECSourceAdapter(SourceAdapter)   source="federal"
  tables.py      FEC_TABLE_DEFINITIONS + LOAD_ORDER (registry pattern from state/tables.py)
  etl.py         FullLoadRunner / IncrementalLoadRunner / ResumeRunner (cycle-aware)
  deltas.py      insert/delete delta application (FEC-specific)
  cli.py         python -m federal.etl full|incremental|resume|list --cycles 2024,2026
```

Shared `core/etl` changes are minimal and additive:
1. `TSVReader(delimiter="|")` — parameterize the split char (ragged-merge uses it too).
2. `_DT_FORMATS += ("%m%d%Y",)`.
3. `load_checkpoint` already has a `source` column (default `'calaccess'`) — use `source='fec'`.
   No schema change needed.

### D4. Cycle range: start with **2024 + 2026** — **approved**

2024 = most recent completed cycle (full national attention, rich CA races);
2026 = current cycle (active deltas). History (1993–2022) is a later disk decision —
the per-cycle design makes it additive.

---

## 5. Schema design (migration `0008_fec_tables.sql`)

All tables in schema `fec`. Column names follow openFEC canonical names (lower_snake in our DDL).

### 5.1 Masters (dimensions)

```sql
fec.fec_committees          -- from cmYY.zip (cycle-agnostic master, upsert by committee_id)
  committee_id TEXT PK      -- C0xxxxxxx
  committee_name TEXT
  treasurer_name TEXT
  street_1, street_2, city, state, zip TEXT
  organization_type TEXT    -- C=Corp L=Labor U=Unincorp T=TradeAssoc I=Individual O=Other
  committee_type TEXT       -- N=party C=HACCC PAC=... U=SingleAuth Y=MultipleAuth P=Presidential H=House S=Senate
  type_designation TEXT
  connected_organization_name TEXT
  fec_election_year TEXT    -- cycle of the source master file
  is_ca_based BOOLEAN GENERATED (state='CA')   -- or plain indexed column

fec.fec_candidates          -- from cnYY.zip
  candidate_id TEXT PK     -- H0AL01055 style
  candidate_name TEXT
  party TEXT
  cycle TEXT
  office TEXT              -- H/S/P
  state TEXT
  district TEXT
  incumbent_status TEXT
  candidate_status TEXT
  principal_committee_id TEXT   -- -> fec_committees
  pcc_address fields...

fec.fec_candidate_pac_linkage  -- from cclYY.zip (candidate ↔ leadership PAC)
```

### 5.2 Facts (partitioned by cycle)

```sql
fec.fec_individual_contributions   -- itcont, PARTITION BY LIST (two_year_transaction_period)
  sub_id BIGSERIAL PK
  committee_id TEXT                  -- recipient committee
  amendment_indicator TEXT           -- N / A / C
  transaction_type_code TEXT
  other_committee_id TEXT
  transaction_id TEXT UNIQUE         -- FEC transaction_id (20 digits) — PRIMARY DEDUP KEY
  conduit_code TEXT
  contributor_type_code TEXT         -- IND / ORG / CCM / PAC / PF
  contributor_name TEXT              -- raw "LAST, FIRST MI"
  contributor_first_name TEXT        -- derived at load
  contributor_last_name TEXT
  contributor_city TEXT
  contributor_state TEXT             -- CA lens key
  contributor_zip TEXT
  contributor_employer TEXT
  contributor_occupation TEXT
  contribution_date DATE             -- from MMDDYYYY
  contribution_amount NUMERIC(30,2)
  aggregate_year_to_date NUMERIC(30,2)
  memo_code TEXT
  memo_reference TEXT
  election_code TEXT
  committee_fec_id TEXT
  image_number TEXT
  receipt_no TEXT
  two_year_transaction_period SMALLINT
  is_redacted BOOLEAN                -- set when amendment blanks PII (2023 rule)
  deleted_at TIMESTAMPTZ             -- tombstone from delete deltas
  src_file TEXT, load_ts TIMESTAMPTZ
```

Same pattern for `fec_other_contributions` (itoth), `fec_conduit_contributions` (itpas2),
`fec_independent_expenditures` (oppexp — includes `spending_type` (independent/coordination),
`supported_opposed` (S/O), targeted `candidate_id/office/state/district`, `communication_type`).

### 5.3 Dedup + CA views (mirrors the `*_deduped` pattern)

```sql
-- Amendment-dedup: latest version per transaction_id, tombstones excluded.
fec.fec_individual_contributions_current AS
  SELECT ... FROM fec_individual_contributions
  WHERE deleted_at IS NULL
  -- keep max version per transaction_id using window function or GROUP BY

-- CA lenses (the product surface for "FEC data in CA"):
fec.v_ca_contributions            -- contributor_state = 'CA'
fec.v_ca_committees               -- committee state = 'CA' OR committee linked to a CA candidate
fec.v_ca_candidates               -- office state = 'CA' (all 52 districts)
fec.v_ca_targeted_ie              -- IE can_office_state = 'CA' (any spender state)
fec.v_ca_money_flows              -- UNION of the four with a `flow_type` tag
```

### 5.4 Cross-jurisdiction link tables (Step 6)

```sql
fec.xj_entity_links        -- resolved person/org across jurisdictions
  link_id, entity_type (person|org),
  fec_key (committee_id|transaction_id|name+addr hash),
  ca_key (filer_id|cmte_id|name+addr hash),
  match_method, match_score, status (pending|confirmed|rejected), reviewed_by, notes
fec.xj_donors              -- materialized: CA-state donors that also appear federally (and vice versa)
```

Reuses `core/etl/entity_resolution.py` (pg_trgm + fuzzystrmatch + merge queue).
Name compatibility is high: both systems store LAST-first; match on
`name + city + zip5` with the existing thresholds, human-review queue before merges.

---

## 6. ETL design

### 6.1 `FECSourceAdapter` (implements the existing `SourceAdapter` ABC)

- `get_source_files()` — enumerate the per-cycle file set from
  `https://www.fec.gov/files/bulk-downloads/<cycle>/` (HEAD each: size/etag/last-modified),
  returning `SourceFileInfo(name="2026/indiv2026.zip", …)`.
- `fetch_file()` — download to `FEC_CACHE_DIR` (new volume `feccache`), stream-friendly
  (httpx streaming, not `response.content` for 4 GB files — note: the state adapter loads
  the whole zip into RAM; the FEC adapter must stream to disk instead).
- `parse_file()` — pipe-delimited reader (delimiter-parameterized `TSVReader`),
  `has_header=False`, positional column mapping from `FEC_TABLE_DEFINITIONS`.
- `source_checksum()` — per-file SHA-256 after download (FEC doesn't publish checksums;
  we compute and checkpoint them like the state pipeline does).
- Delta awareness: `get_deltas(cycle)` → today's `indivYY_insert.zip` / `indivYY_delete.zip`.

### 6.2 `FEC_TABLE_DEFINITIONS` (registry mirroring `state/tables.py`)

Each entry: `code` (e.g. `FEC_INDIV`), `description`, `category`, `source_file_pattern`
(`indiv{yy}.zip` → member `itcont`), `conflict_columns`
(e.g. `(two_year_transaction_period, committee_id, transaction_id)`), `type_coercions`
(`contribution_date: date`, `contribution_amount: numeric`, …), `required_columns`
(`committee_id`, `transaction_id`), `cycle_scoped: True`.

### 6.3 Runners (`federal/etl.py`)

- **Full**: per cycle → masters first (`FEC_CM`, `FEC_CN`, `FEC_CCL`), then facts
  (`FEC_INDIV`, `FEC_OTH`, `FEC_PAS2`, `FEC_OPPEXP`). Same checkpoint-per-table,
  watchdog, dead-letter behavior as the state runner. `load_checkpoint.source='fec'`.
- **Incremental**:
  1. Apply `indivYY_insert.zip` (upsert) and `indivYY_delete.zip` (tombstone by
     `transaction_id`) — daily.
  2. Re-checksum masters + `oth`/`pas2`/`oppexp`; reload changed files only.
  3. FEC updates the bulk files daily; the insert/delete deltas are the documented daily path.
- **Resume**: identical semantics to `state.etl.ResumeRunner` (skip tables whose current
  file hash is checkpointed).
- Memory: the existing streaming loader is O(batch) — fine. The **download** is the new
  memory risk (4 GB zip); stream to disk with a temp-file + rename pattern.

### 6.4 Delta semantics (the genuinely new part)

- Insert delta rows are full `itcont` rows → normal upsert by `transaction_id`.
- Delete delta rows are **full rows** (not just keys) → match on `transaction_id` and
  `UPDATE … SET deleted_at = now()`.
- Views exclude `deleted_at IS NOT NULL` → totals stay correct after redactions.
- Idempotency: re-applying a delete is a no-op; re-applying an insert is an upsert.
- Ordering: deletes after inserts within a cycle run; checkpoint each delta file by hash
  so a day is applied exactly once.

### 6.5 Raw staging tables (new)

To support validation and error handling:
- `fec.raw_cm`, `fec.raw_cn`, `fec.raw_itcont`, etc. — raw files loaded here first
- Validation step checks field counts, required fields, date formats, etc.
- Valid rows upserted to final tables; invalid rows quarantined in `fec.etl_dead_letter`
- Source file metadata tracked in `fec.load_source_files` table (filename, checksum, cycle, loaded_at, row_count, status)

---

## 7. MCP integration

**Add a `fec_*` tool family rather than bolting a `jurisdiction` param onto existing tools.**
The CA tools' contracts are pinned by tests (`test_mcp.py`, `test_mcp_person_tools.py`,
the vendor-attribution invariant test), and the semantics genuinely differ (cycles vs.
derived cycles, single-name field, amendment indicator vs. amend_id).

Proposed tools (all read-only via `cfdb_reader`):

| Tool | Purpose |
|---|---|
| `fec_find_committees(name, state=None, type=None)` | Federal committee lookup (C-ids) |
| `fec_committee_profile(committee_id, cycle=None)` | Totals, treasurer, CA ties |
| `fec_ca_candidates(cycle, office=None, district=None)` | CA federal ballot |
| `fec_top_donors_ca(cycle, min_amount=None, limit=…)` | CA donors to federal committees |
| `fec_contributions_by_donor(name, cycle=None)` | Federal giving by person (single-name aware) |
| `fec_independent_expenditures_ca(cycle, supported_opposed=None)` | IE flows targeting CA |
| `fec_ca_money_summary(cycle)` | Dashboard: CA-in / CA-out / CA-targeted IE totals |
| `cross_jurisdiction_profile(name)` | Person/org across CA state + federal (uses `xj_*`) |

Plus: update `get_server_docs` / `INSTRUCTIONS` with FEC conventions (cycle param,
`amendment_indicator`, redaction caveat, "use `fec_*` for federal, `cfdb_*` for CA state"),
and register the new tables in `describe_table`.

---

## 8. Docs to write (repo convention)

- `docs/fec_data_dictionary.md` — every table/column (mirror `data_dictionary.md` style).
- `docs/fec_caveats.md` — amendment indicator semantics, delete-delta/redaction rule,
  conduit/earmark double-count warnings, FEC-Online overlap, "IE aggregates vs. line items",
  cycle vs. calendar-year queries, CA district renumbering (2020 redistricting: district
  numbers shift between 2020 and 2022 cycles — never compare district strings across cycles).
- `docs/fec_discovered_tables.md` — Step-1 discovery notes (like `discovered_tables.md`):
  exact column layouts, row counts, sample rows per file.
- README: flip "Federal (Planned)" → live; document `--cycles`, `FEC_API_KEY` (optional),
  new compose services, updated disk/RAM guidance.

---

## 9. Testing strategy

- **Unit**: pipe-delimited parser (ragged rows, embedded commas/pipes, NULs); `MMDDYYYY`
  coercion; cycle derivation; delta upsert/tombstone idempotency; CA view predicates.
- **Integration** (SQLite + Postgres like today): load tiny fixture files per FEC table;
  assert dedup view invariants (amended transaction counted once; deleted transaction
  excluded; insert-then-delete ordering).
- **Golden numbers** (`samples/fec_golden_numbers.json`): cross-check against
  (a) FEC's own published state totals on fec.gov for CA 2024/2026, and
  (b) OpenFEC API totals (`/v1/schedules/schedule_a/?state=CA&two_year_transaction_period=…`
  with a CI-provided `FEC_API_KEY`; skip gracefully when absent).
- **MCP tests**: new tools' result shapes; the one-attribution-basis invariant extended
  to the `fec_*` family.

---

## 10. Ops / deployment

- `docker-compose.yml`: add `fec-etl` service (same image, `entrypoint: python -m federal.etl`)
  and a `feccache` volume; keep the CA `etl` service untouched.
- `.env.example`: `FEC_CYCLES=2024,2026`, `FEC_CACHE_DIR=/app/federal/cache`,
  `FEC_API_KEY=` (optional, validation only).
- Scheduler (`core/workflows/scheduler.py`): add a federal step — apply daily insert/delete
  deltas + checksum-check the other files. Cron-friendly, same exit-code contract.
- Disk/RAM: ~5–7 GB/cycle compressed download; DB ~100–200 GB for two cycles national
  (indexes included). ETL RAM unchanged (~8 GB) **if** downloads stream to disk.
- `install.sh`: add `--fec` flag (or a second phase prompt) so CA-only installs are unaffected.

---

## 11. Phased milestones

| Step | Deliverable | Acceptance criteria |
|---|---|---|
| **1. Discovery + schema** | `0008_fec_tables.sql`, `federal/tables.py`, `docs/fec_discovered_tables.md` | Layouts verified against real files for both cycles; dedup key (transaction_id) confirmed on real amendment chains |
| **2. Masters load** | `FECSourceAdapter` + full load of `cm`/`cn`/`ccl` | 20K+ committees, 8K+ candidates queryable; CA counts sane vs. FEC site |
| **3. Contributions + CA views** | `itcont` full load (2 cycles), dedup + `v_ca_*` views | CA 2024 totals match FEC published figures within rounding; load resumable |
| **4. Deltas** | `deltas.py` + incremental runner | Insert/delete applied idempotently; re-run = no-op; totals stable across days |
| **5. IEs + other schedules** | `oppexp`, `itoth`, `itpas2` | CA-targeted IE totals match FEC site; conduit memo caveats documented |
| **6. MCP tools + docs** | `fec_*` tools, updated `get_server_docs`, caveats | New MCP tests green; an agent can answer "Who are the top CA donors to federal races in 2024?" from docs alone |
| **7. Cross-jurisdiction** | `xj_*` tables + `cross_jurisdiction_profile` | Sample humans/orgs matched with review queue; precision spot-checked ≥ 95% |
| **8. History expansion** | Cycles 1993–2022 (optional) | Additive; no re-plumbing |

Suggested ordering keeps each step independently shippable and verifiable — same rhythm
the CAL-ACCESS side used.

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| FEC file layout drift between cycles (older cycles differ) | Pin cycles in `FEC_TABLE_DEFINITIONS`; per-cycle layout overrides; discovery doc per cycle |
| `fec.gov` redirect / S3 bucket changes | Adapter reads base URL from env (`FEC_BULK_BASE_URL`); checksums catch truncation; resume works |
| Delete-delta key mismatch (redactions) | Step 1 verifies the exact delete key (`transaction_id`) on real data before building `deltas.py` |
| FEC Online overlap double-counting | P2 study before loading `weball/webk/webl`; receipt_no collision check |
| Disk under-budget for national load | CA-filtered fallback mode via `row_filter` (~10–20 GB) — decision D2 |
| API key rate limits during validation | Bulk-first design; API used only for golden-number checks |
| Cross-jurisdiction false positives | Human-review merge queue (existing), conservative thresholds, `status` gating in views |
| Breaking CA pipeline | Zero edits to `state/` semantics; shared-core changes are additive + tested |

---

## 13. Open decisions — RESOLVED (2026-09-21)

| # | Decision | Resolution |
|---|---|---|
| 1 | Schema placement | **Separate `fec` Postgres schema** |
| 2 | Load scope | **Full national** for 2024+2026 (~100–200 GB) — need to capture out-of-state PACs running IEs in CA |
| 3 | Cycle range | **2024 + 2026 only** to start |
| 4 | MCP surface | **`fec_*` tool family** (new tools, not params on existing) |
| 5 | API key | **Yes** — user registered a free api.data.gov key for validation tests |

All decisions locked in. Step 1 (discovery + schema) is approved to begin.
