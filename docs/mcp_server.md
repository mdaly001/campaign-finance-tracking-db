# Campaign Finance Database — MCP Server Guide

Read-only MCP (Model Context Protocol) server over the **CAL-ACCESS**
California Secretary of State campaign finance disclosure database
(PostgreSQL 16). 15 query tools covering contributions, expenditures,
committees, people, ballot measures, filing deadlines, and the 24-hour
rapid-disclosure reports.

> **Agents: this document is also served in-band.** Call the
> `get_server_docs` tool after connecting — it returns exactly this text,
> so you never need repository access to get up to speed.

## 1. Attaching an agent

The server speaks MCP over **SSE**. Point any MCP client at:

```
http://<host>:9527/sse
```

Example client config (Claude Desktop / most harnesses):

```json
{
  "mcpServers": {
    "cfdb": {
      "url": "http://<host>:9527/sse"
      }
  }
}
```

Operational details:

| Item | Value |
|---|---|
| Default port | `9527` (override: `MCP_PORT` env var or `--port`) |
| SSE endpoint | `/sse` (messages arrive on `/messages/`) |
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
| `find_committees` | Committee ID(s) from a (partial) name | `name`, `limit` |
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
    filings (current extract: reports received through ~2026-08-24).
11. **Empty until built.** `filing_calendar` and `election_results`
    currently have no rows; `ballot_measures_cd` covers 2000–2009 only.
    Recent ballot measures resolve via committee names, not measure IDs.

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

## 5. Repository pointers (if you have source access)

- `docs/data_caveats.md` — deep caveats with worked SQL (no personal
  names), including the 24-hour vendor-resolution playbook.
- `docs/data_dictionary.md` — full table/column dictionary.
- `core/mcp/tools.py` — tool implementations and the matching helpers
  (`_person_predicate`, `_vendor_regex`, `_rowlist_dedup_sql`).
- `core/mcp/db.py` — connection config (read-only role, pool settings).
