# Campaign Finance Database — MCP Server Guide

Read-only MCP (Model Context Protocol) server over the campaign finance
disclosure warehouse (PostgreSQL 16). 29 query tools spanning two sources:

- **California — CAL-ACCESS** (Secretary of State): contributions,
  expenditures, committees, people, ballot measures, filing deadlines, the
  24-hour rapid-disclosure reports.
- **Federal — FEC** (OpenFEC API): candidate-targeted independent
  expenditures (Schedule E) — for/against totals, top spenders, and
  spender/race breakdowns.

Plus a guarded read-only SQL escape hatch (`run_sql`) for edge-case queries
no dedicated tool covers.

> **Agents: this document is also served in-band.** Call the
> `get_server_docs` tool after connecting — it returns exactly this text,
> so you never need repository access to get up to speed.

## 1. Attaching an agent

The server speaks MCP over **Streamable HTTP**. Point any MCP client at:

```
http://<host>:9527/mcp
```

Example client config (Claude Desktop / most harnesses):

```json
{
  "mcpServers": {
    "cfdb": {
      "url": "http://<host>:9527/mcp"
      }
  }
}
```

Operational details:

| Item | Value |
|---|---|
| Default port | `9527` (override: `MCP_PORT` env var or `--port`) |
| Transport | Streamable HTTP at `/mcp` (JSON-RPC POSTs; SSE-style responses) |
| Database auth | `cfdb_reader` (read-only role, `READ ONLY` transactions) |
| DB config | `DATABASE_URL`, or `DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT`/`DB_NAME` |
| Log level | `LOG_LEVEL` env var (default `INFO`) |
| Writes | None. The role cannot write; unredacted data is not exposed. |

Start the server yourself (if you run your own instance):

```bash
DATABASE_URL="postgresql://cfdb_reader:***@127.0.0.1:5432/cfdb" \
MCP_PORT=9527 python -m core.mcp.server
```

## 2. Tool catalog

| Tool | What it answers | Key arguments |
|---|---|---|
| `get_server_docs` | This guide (call first) | — |
| `describe_table` | Columns + gotchas for any table/view, before ad-hoc reasoning | `table_name` |
| `run_sql` | Ad-hoc read-only SQL (single `SELECT`/`WITH`/`EXPLAIN` statement) for edge cases no dedicated tool covers — guarded: read-only role, 15 s timeout, capped rows | `sql`, `row_limit?` |
| `find_committees` | Committee ID(s) from a (partial) name | `name`, `limit` |
| `fuzzy_name_search` | Resolve-first lookup of a person or organization: every transaction the name touched, grouped by canonical entity — whole-word matching, dedup-safe, honest empty when nothing resolves | `name_query`, `start_date?`, `end_date?`, `entity_type?`, `limit?` |
| `committee_profile` | Name, type, totals (contributions incl. 24-hr, expenditures, cash) | `committee_id`, `as_of_date?` |
| `contributions_by_donor` | All contributions by a donor in a cycle | `donor_name`, `cycle`, `include_aliases?` |
| `top_donors_for_committee_or_candidate` | Top N donors to a committee | `committee_id`, `cycle`, `limit?` |
| `donor_watch_since` | Contributions from a donor since a date (incl. 24-hr, de-duped) | `donor_name`, `since_date` |
| `committee_outlays_to` | A committee's payments to one vendor in a cycle | `committee_id`, `vendor_name`, `cycle` |
| `vendor_revenue` | A vendor's total revenue across all committees | `vendor_name`, `limit?` |
| `committees_paying_vendor` | Which committees paid a vendor, ranked | `vendor_name`, `candidate_only?` |
| `measure_spending` | Spending on a ballot measure, ranked by committee | `measure_id` |
| `payments_to_person` | **Every role a person plays**: paid-as-vendor, gave-as-donor, ran-as-candidate — one call | `person_name`, `since_date?`, `roles?`, `limit?` |
| `rapid_expense_vendors` | Recover payee names for a committee's 24-hr (Form 496) expenses | `committee_id`, `since_date?` |
| `upcoming_filings` | Filing deadlines within N days (from `filing_period_cd`) | `committee_id`, `days_ahead?` |
| `filing_due_soon` | Scraper-tracked deadlines within N days | `days_ahead?` |
| `total_expenditures` | Total spend by a committee in a cycle (deduped Form E/F461P5; optional refund exclusion) | `committee_id`, `cycle`, `exclude_refunds?` |
| `refunds_to_donors` | "Return of contribution" refund lines by a committee in a cycle | `committee_id`, `cycle`, `limit?` |
| `data_freshness` | Snapshot freshness: newest transaction dates (corrupt future-dated rows excluded), last ETL load, row counts | — |
| `fec_find_candidate` | **Federal.** Resolve a candidate name to FEC `candidate_id`(s) from Schedule E; substring **or fuzzy trigram** match (typos/reordered names), each hit scored `match` 0–1; optional state/district/cycle | `name`, `state?`, `district?`, `cycle?`, `limit?`, `min_similarity?` |
| `fec_ie_summary` | **Federal.** De-duplicated for/against independent-expenditure totals for a candidate; optional date window (start inclusive, end exclusive) | `candidate_id`, `cycle?`, `start_date?`, `end_date?` |
| `fec_top_spending` | **Federal.** Top N committees spending for or against a candidate (de-duplicated) | `candidate_id`, `side?` (`against`/`for`), `cycle?`, `limit?` |
| `fec_spender_breakdown` | **Federal.** What a spending committee bought — by payee, purpose (ad buy vs production), and target candidate | `spender_id`, `candidate_id?`, `cycle?`, `limit?` |
| `fec_race_overview` | **Federal.** All independent expenditures in a state/district race, by candidate and for/against side | `state`, `district`, `cycle?`, `limit?`, `election_type?` |
| `fec_ie_by_org` | **Federal.** Every (org → candidate → side) IE combination above a threshold, one row each — a committee spending for one candidate and against another shows as two rows, with a human `purpose` string | `state`, `district`, `cycle?`, `election_type?`, `min_amount?`, `limit?` |
| `entity_contributions` | **Cross-source.** Who gave TO an entity (money IN), across BOTH California (CAL-ACCESS) and federal (FEC) filings; entity resolved by name in both registries | `entity`, `year?`, `min_amount?`, `limit?` |
| `entity_expenditures` | **Cross-source.** What an entity spent (money OUT), across BOTH California and federal filings; federal by candidate+side, state by payee/purpose | `entity`, `year?`, `min_amount?`, `limit?` |
| `returned_contributions` | **Cross-source.** Contributions an entity RETURNED/REFUNDED to donors — state: Schedule E refund/return expenditures (recipient = payee); federal: negative contributions. Reconciles what a committee kept vs. received | `entity`, `year?`, `min_amount?`, `limit?` |

### Choosing a tool — common questions

- **"How much has X been paid since 2016?"** (person, not committee) →
  `payments_to_person(person_name="X", since_date="2016-01-01", roles="payee")`.
  Returns the payments plus a `blind_spot` count of 24-hour expense lines
  from the paying committees; follow up with `rapid_expense_vendors` on
  those committees if the blind-spot count is material.
- **"Which committees pay vendor V?"** → `committees_paying_vendor(vendor_name="V")`.
- **"How much did committee C spend on V in 2024?"** →
  `committee_outlays_to(committee_id=..., vendor_name="V", cycle=2024)`.
- **"Who gave money to candidate C in 2024?"** →
  `find_committees` (if you only have the name) then
  `top_donors_for_committee_or_candidate(..., cycle=2024)`.
- **"Did committee C's 24-hr reports hide any big vendor spend?"** →
  `rapid_expense_vendors(committee_id=...)`.
- **"Who paid whom / what did X pay or receive?"** (person or org, not a
  committee id) → `fuzzy_name_search(name_query="X")` — resolves the name to
  canonical filer entities first (name + alias tables), then reports their
  transactions, per-year totals, and top recipients; never guesses a first
  match.

## 3. Data conventions (read this before trusting any result)

1. **Individuals are stored last-first.** `*_naml` holds the LAST name,
   `*_namf` the FIRST (e.g. payee `naml='Daly'`, `namf='Michael Gomez'`).
   Organizations sit in the `naml` field. All name tools are field-aware
   and word-anchored, so searching "Daly" will not match "Odalys" or
   "Brendalyn" — but when you write SQL yourself, match per field with
   `~* '\mDaly'` (Postgres ARE flavor), not a single-field
   `ILIKE '%michael daly%'` (that misses last-first storage).
2. **No year column.** Election "cycles" are derived from the transaction
   date (`rcpt_date` / `expn_date`).
3. **`cmte_id` on receipt lines is the DONOR committee**, not the
   recipient. Committee attribution must go through
   `filer_filings_cd` → `filer_xref_cd` (the tools do this; ad-hoc SQL
   must too).
4. **Contribution tools read `receipts_all`**, which unions `rcpt_cd` +
   `s497_cd` (24-hr large gifts) + `s498_cd` (24-hr receipts) with
   cross-source de-duplication — a gift reported in both a 24-hour and a
   periodic report counts once.
5. **24-hour EXPENDITURE reports (Form 496 / `s496_cd`) have no payee
   name** — only amount, date, and a free-text description that is usually
   a generic label ("TELEVISION ADS", "MAILER"), not a payee. Any
   vendor answer based on `expn_cd` is therefore a **lower bound**. Use
   `rapid_expense_vendors` to recover payees (80–97% of lines resolve by
   matching the periodic re-filing on date + exact amount).
6. **Name fragmentation.** The same vendor/donor appears under many
   spellings; tools match by anchored phrase, but results can still be
   under-counts. Inspect the distinct matched name strings before drawing
   conclusions.
7. **`filername_cd` is inflated** — one row per (name × contact) combo
   (a filer appears ~10×). `DISTINCT ON (filer_id)` for a single name.
8. **`filer_filings_cd` pairs can duplicate** — joins that fan out on it
   inflate counts; use `DISTINCT` or `EXISTS`.
9. **Corrupt dates exist.** A minority of rows carry NULL or implausible
   dates (1900/3000 era). Never use unbounded `MAX(date)` for freshness;
   the snapshot's newest *received* reports are the freshness signal.
10. **Snapshot, not live.** The database is a periodic extract of SOS
    filings; the snapshot date moves with each ETL load. Call
    `data_freshness` to get the current newest receipt/expenditure dates
    (corrupt future-dated rows are excluded and counted separately), the
    last ETL load, and row counts before quoting any "current" totals.
11. **Empty until built.** `filing_calendar` and `election_results`
    currently have no rows; `ballot_measures_cd` covers 2000–2009 only.
    Recent ballot measures resolve via committee names, not measure IDs.

## 3b. Federal (FEC) data conventions

1. **Always read the de-duplicated, name-resolved view.** Federal IE tools
   query `fec.fec_ie_targeted_resolved` (a materialized view built on
   `fec.fec_ie_targeted_dedup`), which collapses **two** independent
   duplication sources:
   - **Layer 1 — F24 notice vs F3X report.** The FEC reports the same
     expenditure in both the F24 48-hour notice and the later F3X periodic
     report (same `transaction_id`). Collapsed to one row per
     `(spender_id, transaction_id)`, F3X actual preferred.
   - **Layer 2 — cross-filing re-filing.** The same expenditure re-appears
     in two *different* filings with a *different* `transaction_id` (a
     committee re-reports a line in a later/amended report). `most_recent`
     does NOT catch this. Collapsed by
     `(spender_id, candidate_id, expenditure_date, expenditure_amount)`
     keeping `max rows-per-filing` (amendment `A` / latest filing
     preferred) — mirroring the California `receipts_all` pattern. This
     preserves legitimate same-filing multi-line items while dropping the
     re-filed copies.
   Summing the raw `fec.fec_ie_targeted` table double-counts both ways.
   Never sum the base table for a total.
   > **Refresh after load:** the resolved view is materialized, so run
   > `REFRESH MATERIALIZED VIEW CONCURRENTLY fec.fec_ie_targeted_resolved;`
   > after every IE load.
1b. **Per-candidate totals use `resolved_candidate_id`, not the raw
   `candidate_id`.** Some IEs carry a `candidate_name` but a NULL
   `candidate_id`, and the same candidate is reported under different name
   orderings ("WILPERT, MARNI VON" / "VON WILPERT, MARNI"). The resolved
   view folds NULL-id records into their named candidate via an **exact**
   canonical-name key (uppercase, strip punctuation, sort tokens) and merges
   the name variants — but only when the canonical name maps to exactly one
   candidate (a wrong merge is worse than an unresolved row). Grouping by the
   raw `candidate_id` drops the NULL-id records and splits the variants, so
   it under-reports.
1c. **Fuzzy matching is for *finding*, never for *summing*.**
   `fec_find_candidate` uses pg_trgm `similarity` (with a `min_similarity`
   threshold, default 0.30) so typos and partial names surface, each hit
   scored `match` 0–1 for the caller to judge. The aggregation path never
   uses fuzzy — a false-positive merge would silently corrupt a total.
2. **`cycle` is the FEC two-year period** (stored in `election_year`, e.g.
   `2026`). Omit it to span every loaded cycle. Federal data is loaded
   per-cycle from the OpenFEC API (`federal/load_ie_api.py`), so only
   loaded cycles return rows.
3. **`support_oppose`**: `S` = independent expenditure **for** the
   candidate, `O` = **against**.
4. **`candidate_id` is the target**, `spender_id` is the committee making
   the independent expenditure. A spender can target many candidates; use
   `fec_spender_breakdown` to see the spread.
5. **Purpose is free text** (e.g. "TELEVISION ADVERTISING BUY" vs
   "…PRODUCTION"); `fec_spender_breakdown` groups by it so buys and
   production are separated rather than lumped.
6. **Date window is half-open**: `start_date` inclusive, `end_date`
   exclusive (ISO `YYYY-MM-DD`), so a June query is
   `start_date="2026-06-01", end_date="2026-07-01"`.

## 4. Worked examples

**Q: All payments to "Michael Gomez Daly" since 2016.**

```json
{ "name": "payments_to_person",
  "arguments": { "person_name": "Michael Gomez Daly",
                 "since_date": "2016-01-01", "roles": "payee" } }
```

→ 3 payments from one committee (doorhanger postage), plus
`blind_spot.s496_lines_for_paying_committees` telling you how many
unnamed 24-hour expense lines that committee has.

**Q: Who were the payees behind that committee's 24-hour spend?**

```json
{ "name": "rapid_expense_vendors",
  "arguments": { "committee_id": "C0695132" } }
```

→ resolved lines (date, amount, payee) + unresolved lines +
`resolution_pct`.

**Q: What's in `s497_cd`?**

```json
{ "name": "describe_table", "arguments": { "table_name": "s497_cd" } }
```

→ columns (amount is `amount`, date is `ctrib_date`) + the curated gotcha
note.

**Q (federal): How much was spent for and against a candidate in June?**

```json
{ "name": "fec_find_candidate", "arguments": { "name": "Villegas", "state": "CA", "district": "22" } }
```
→ `candidate_id: "H6CA22190"`, then

```json
{ "name": "fec_ie_summary", "arguments": { "candidate_id": "H6CA22190", "cycle": 2026, "start_date": "2026-06-01", "end_date": "2026-07-01" } }
```

→ de-duplicated `for_total` / `against_total` (the F24/F3X double-count
already collapsed).

**Q (federal): Who spent the most against that candidate, and on what?**

```json
{ "name": "fec_top_spending", "arguments": { "candidate_id": "H6CA22190", "side": "against", "cycle": 2026, "limit": 5 } }
```
→ ranked spenders; then `fec_spender_breakdown(spender_id=...)` for the
payees/purposes behind any one of them.

## 5. Repository pointers (if you have source access)

- `docs/data_caveats.md` — deep caveats with worked SQL (no personal
  names), including the 24-hour vendor-resolution playbook.
- `docs/data_dictionary.md` — full table/column dictionary.
- `core/mcp/tools.py` — California tool implementations and the matching
  helpers (`_person_predicate`, `_vendor_regex`, `_rowlist_dedup_sql`).
- `core/mcp/federal_tools.py` — federal (FEC) Schedule-E tools; all query
  the `fec.fec_ie_targeted_dedup` view.
- `federal/load_ie_api.py` — the resumable OpenFEC API loader that
  populates `fec.fec_ie_targeted` (cursor pagination, `sub_id` upsert).
- `migrations/0017_fec_ie_targeted_dedup_view.sql` /
  `0018_fec_ie_targeted_dedup_refile.sql` — the two-layer de-duplicated
  Schedule-E view the federal tools read.
- `migrations/0019_fec_ie_candidate_resolution.sql` — the `canonical_name`
  function and the materialized `fec.fec_ie_targeted_resolved` view (NULL-id
  fold + name-variant merge + trigram index) the federal tools query.
- `core/mcp/db.py` — connection config (read-only role, pool settings).
