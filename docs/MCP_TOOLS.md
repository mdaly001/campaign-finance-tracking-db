# MCP Tools Cheat Sheet — 29 read-only tools

Quick index for AI agents: **what exists for what kind of query**. Full
conventions and worked examples: `docs/mcp_server.md` (also served in-band
via `get_server_docs`).

> **Start every time-sensitive session with `data_freshness()`** — it reports
> newest receipt/expenditure dates and last ETL load. Never quote a "total"
> without knowing how fresh the underlying snapshot is.

## Query intent → tool

### California — contributions
| Question | Tool |
|---|---|
| "What did donor X give?" | `contributions_by_donor(donor_name, cycle)` |
| "Top donors to this committee?" | `top_donors_for_committee_or_candidate(committee_id, cycle)` |
| "Everything donor X gave since <date>?" (incl. 24-hr, deduped) | `donor_watch_since(donor_name, since_date)` |
| "Who gave TO this person/org?" (both CA **and** federal) | `entity_contributions(entity, year?)` |

### California — expenditures
| Question | Tool |
|---|---|
| "How much did committee X spend in cycle Y?" | `total_expenditures(committee_id, cycle)` — IECs return the **Form D** total plus `form_d_total`/`form_e_total` split |
| "…excluding refunds?" | `total_expenditures(committee_id, cycle, exclude_refunds=true)` |
| "What did committee X refund?" | `refunds_to_donors(committee_id, cycle)` |
| "Committee X's payments to vendor Y?" | `committee_outlays_to(committee_id, vendor_name, cycle)` |
| "24-hr (Form 496) payees for committee X?" | `rapid_expense_vendors(committee_id)` — 496 lines carry **no payee**; this recovers them by date+amount (80–97%) |

### Vendors
| Question | Tool |
|---|---|
| "Vendor Y's total revenue across all committees?" | `vendor_revenue(vendor_name)` |
| "Which committees pay vendor Y?" | `committees_paying_vendor(vendor_name, candidate_only?)` |

### People & organizations
| Question | Tool |
|---|---|
| "Every role this name plays" (vendor/donor/committee/candidate) | `payments_to_person(person_name)` |
| "Resolve a fuzzy name → all transactions it touched" | `fuzzy_name_search(name_query)` — undated rows land in `totals_undated`, never crash |
| "Committee ID from a (partial) name?" | `find_committees(name)` |
| "Full profile of a committee (type, totals, cash)?" | `committee_profile(committee_id, as_of_date?)` |
| "What did this org spend?" (CA **and** federal) | `entity_expenditures(entity, year?)` |
| "What did this org refund/give back?" (CA **and** federal) | `returned_contributions(entity, year?)` |

### Ballot measures
| Question | Tool |
|---|---|
| "Spending on measure <id>, by committee" | `measure_spending(measure_id)` |

### Filings & deadlines
| Question | Tool |
|---|---|
| "What's this committee's next filing deadline?" | `upcoming_filings(committee_id, days_ahead?)` |
| "Everything due in the next N days?" | `filing_due_soon(days_ahead=7)` |

### Federal — independent expenditures (FEC Schedule E, for/against)
| Question | Tool |
|---|---|
| "Resolve a federal candidate name → candidate_id" | `fec_find_candidate(name, state?, district?, cycle?)` |
| "How much for/against candidate X?" (whole cycle) | `fec_ie_summary(candidate_id)` |
| "…in a date window?" | `fec_ie_summary(candidate_id, start_date, end_date)` — start inclusive, end exclusive |
| "Top PACs attacking candidate X?" | `fec_top_spending(candidate_id, side='against')` |
| "What did PAC Z buy, and against whom?" | `fec_spender_breakdown(spender_id)` |
| "Whole race picture (state + district)" | `fec_race_overview(state, district)` |
| "Every org→candidate→side above $N in a race" | `fec_ie_by_org(state, district, min_amount?)` — **no date filter**; for windows use `fec_ie_summary` per candidate or `run_sql` on `fec.fec_ie_targeted_dedup` |

### Metadata & escape hatch
| Question | Tool |
|---|---|
| "Columns + gotchas for table T before writing SQL" | `describe_table(table_name)` |
| "Anything no dedicated tool covers" | `run_sql(sql)` — single SELECT/WITH/EXPLAIN, 15 s timeout, read-only role |
| "How fresh is this data?" | `data_freshness()` |
| "The full guide" | `get_server_docs()` |

## Caveats that change numbers — read before quoting

**California:**
- Names are stored **last-first** (`naml` = last); tools handle matching, raw SQL must too.
- Contribution tools read the de-duplicated `receipts_all` view — a gift in
  both a periodic and a 24-hr filing counts **once**.
- **IEC Form D vs E**: `total_expenditures` reports the Form D (beneficiary)
  total. Form E vendor lines are **undated** and excluded from the date-filtered
  total — the split is disclosed in `form_d_total` / `form_e_total` + notes.
- Form 496 (24-hr expenditure) lines have **no payee name** — vendor answers
  from raw 496 data are a lower bound; use `rapid_expense_vendors`.

**Federal (FEC targeted IE):**
- Tools read `fec.fec_ie_targeted_dedup` (F24-notice/F3X collapse + cross-
  filing re-filing collapse). **Never** sum `fec_ie_targeted` raw — it
  double-counts.
- **Undated rows exist in bulk data** (`expenditure_date IS NULL` — many
  48-hr-notice estimates). Date-window queries silently exclude them; check
  the undated remainder with `run_sql` before calling a window total complete.
- **Name casing varies** (`VALADAO, DAVID` vs `Valadao, David`) — group with
  `upper(candidate_name)` in raw SQL.
- `is_notice` / `filing_form` are populated only by the API loader
  (`federal/load_ie_api.py`); rows from the bulk CSV have them NULL.
- Candidate resolution uses `fec.fec_ie_targeted_resolved` (matview). If it
  looks empty/stale, it needs `REFRESH MATERIALIZED VIEW` after a load.

**Freshness / operations:**
- Scheduled updates: `ops/updater.py` — incremental Mon–Fri 01:00, full
  reload Sat 02:00 (Pacific). See `ops/cron-compose.yml`.
- `data_freshness()` caps reported dates at today; future-dated rows are
  data corruption and counted separately, not reported as freshness.
