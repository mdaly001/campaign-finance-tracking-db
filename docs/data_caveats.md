# Data Caveats & Known Limitations

Known quirks of the CAL-ACCESS dataset and this system that can silently distort
query results. Read this before interpreting any totals, rankings, or "who paid
whom" answers. None of these are bugs in the ETL — they are properties of how
the Secretary of State captures (or fails to capture) the data.

---

## 1. Rapid (24-hour) disclosure

### 1.1 24-hour contributions are unioned — with dedup
Large contributions to certain committees must be reported within 24 hours
(forms S-497, and S-498-R for certain payors). The same gifts are later
re-reported in the committee's periodic reports, so a naive union of
`rcpt_cd` + `s497_cd` + `s498_cd` double-counts.

- The `receipts_all` view (migration 0002) unions the three sources; every
  row carries a `source` column.
- Contribution tools (`contributions_by_donor`,
  `top_donors_for_committee_or_candidate`, `committee_profile`,
  `donor_watch_since`) dedup per (donor key, date, amount), keeping one row
  per source present and preferring `rcpt_cd` when a gift appears in both.

**Watch out for:** if you write raw SQL against these tables directly (instead
of the tools), you must apply the same dedup or you will over-count recent
activity from rapid-disclosure committees.

### 1.2 24-hour *expenditures* carry no payee name (structural blind spot)
The 24-hour expenditure report (form 496, table `s496_cd`) records amount,
payment date, and a **free-text description only** — the SOS export has no
payee name field for it (unlike the periodic expenditure table `expn_cd`,
which carries `payee_naml`/`payee_namf`). Consequences:

- Vendor queries (`committees_paying_vendor`, `vendor_revenue`,
  `committee_outlays_to`) are structurally blind to any spend disclosed only
  through form 496, no matter how large.
- 24-hour expenditure lines are typically re-reported in the next periodic
  report, where proper payee names appear — so vendor data for
  rapid-disclosure committees arrives one reporting cycle late.

**Watch out for:** "committee X has paid no vendors" may really mean
"committee X has paid vendors only through rapid disclosure so far" —
check `s496_cd` before concluding a committee has spent nothing.

#### Deciphering vendors from 24-hour expenditure lines

A practical three-step playbook (all queries take a `:filer_id`):

**Step 1 — Blind-spot check.** Does this filer have 24-hour expense lines
that vendor tools can't see?

```sql
SELECT COUNT(*) AS rapid_expense_lines,
       COALESCE(ROUND(SUM(s.amount)), 0) AS total
FROM s496_cd s
WHERE s.filing_id IN (
    SELECT filing_id FROM filer_filings_cd WHERE filer_id = :filer_id
);
```

If this is 0, the standard vendor tools already see everything. If it's
large relative to the filer's `expn_cd` totals, keep going.

**Step 2 — Resolve payees by re-matching against periodic reports.**
Periodic reports re-file the same dollars with payee names, and the amount
reappears to the exact cent — so a (payment date, amount) match within the
same filer recovers the vendor for the large majority of 24-hour lines.
Measured on the five largest 24-hour reporters in the database, **80–97% of
form-496 lines resolve** this way; the remainder is usually recent spend
whose periodic re-report hasn't been filed yet, or lines that were
re-reported in a different form.

```sql
WITH f AS (
    SELECT DISTINCT filing_id
    FROM filer_filings_cd
    WHERE filer_id = :filer_id
),
s496 AS (
    SELECT s.exp_date::date AS d, s.amount
    FROM s496_cd s
    WHERE s.filing_id IN (SELECT filing_id FROM f)
),
expn AS (
    SELECT DISTINCT
           e.expn_date::date AS d,
           e.amount,
           TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, '')) AS payee
    FROM expn_cd e
    WHERE e.filing_id IN (SELECT filing_id FROM f)
      AND TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, '')) <> ''
)
SELECT s.d AS payment_date,
       s.amount,
       COALESCE(NULLIF(p.payee, ''), '(unresolved — see step 3)') AS payee
FROM s496 s
LEFT JOIN expn p
       ON p.d = s.d AND p.amount = s.amount
ORDER BY s.d DESC NULLS LAST;
```

Two matching caveats:

- If one (date, amount) pair corresponds to *several* payees in the periodic
  table, the line prints once per candidate — treat such rows as ambiguous
  rather than picking arbitrarily.
- The match is within-filer by design; two unrelated committees paying the
  same amount on the same day can never be conflated.

**Step 3 — Mine the description text (last resort).** For the lines that
didn't resolve, the free-text description is all that remains. Grouping
recurring descriptions at least reveals the *shape* of the spending
(recurring identical descriptions are typically a standing vendor
engagement, e.g. a monthly retainer):

```sql
SELECT TRIM(LOWER(s.expn_dscr)) AS description,
       COUNT(*) AS occurrences,
       ROUND(SUM(s.amount)) AS total,
       MIN(s.exp_date)::date AS first_seen,
       MAX(s.exp_date)::date AS last_seen
FROM s496_cd s
WHERE s.filing_id IN (
    SELECT filing_id FROM filer_filings_cd WHERE filer_id = :filer_id
)
  AND s.expn_dscr IS NOT NULL AND TRIM(s.expn_dscr) <> ''
GROUP BY 1
ORDER BY occurrences DESC;
```

Be honest about the limits: in practice these descriptions are usually
**generic category labels** (mail production, polling, cable, consulting,
field) or internal memo codes, and rarely contain a payee's name at all.
They tell you *what was bought*, not *who was paid*. For those lines, the
only reliable answer is to re-run step 2 after the committee's next periodic
report lands.

---

## 2. Name matching

### 2.1 Vendor names are heavily fragmented
The same payee appears under many spellings across filings: different
capitalization, "LLC" vs "LLC," vs "Inc.", "dba …" prefixes, and word
reordering. One large vendor alone appeared under 16+ spelling variants.
Exact string matches miss most of a vendor's history.

**Guidance:** use word-boundary, case-insensitive matching on the full
payee name (last field + first field concatenated, as the tools do). Even
then, review the distinct matched payee strings before trusting a total —
the match may include genuinely different entities that happen to share a
word (e.g., a place name appearing in unrelated businesses).

### 2.2 Individual consultants are stored last-name-first
For natural-person payees, `payee_naml` holds the **last** name and
`payee_namf` the **first** name. Searching only the "name" field for a
person's first name finds nothing.

**Guidance:** always concatenate both fields (`naml || ' ' || namf`) and
match case-insensitively; for people, expect the reversed order.

### 2.3 Donor name variants are NOT merged
Donor matching keys on the name as filed. Name-format variants of the same
human — different capitalization, included or omitted middle initial,
hyphenation differences — are distinct donor keys and are **not**
auto-merged. Totals for a well-known donor can therefore be understated.

**Guidance:** treat "top donor" numbers for individuals as lower bounds
unless entity resolution has been applied. (Entity resolution of named
donors is a documented out-of-scope item.)

---

## 3. Committee identity

### 3.1 Committees are renamed, not replaced
A committee is a permanent entity with one `filer_id` for its life; its
name changes whenever it joins or leaves an issue campaign (the measure
identifier is commonly appended to the name). The database shows the
**current** name, so a name search for a campaign returns the committee's
*entire history under all former names*.

**Guidance:** when measuring "what did campaign X spend/pay?", filter
expenditure/contribution rows by **date** (≥ when the campaign started or
the filer registered), not just by the name match. Unfiltered name-scoped
totals can include a decade of prior-campaign activity.

### 3.2 `filername_cd` row count is inflated
The filer name table carries one row per (name × contact detail)
combination — up to ~11× the number of distinct filers. Aggregating or
counting from it without dedup inflates everything.

**Guidance:** always `DISTINCT ON (filer_id)` (or group by `filer_id`)
before counting filers or joining names.

### 3.3 `cmte_id` on detail tables is the *donor* committee's ID
On `rcpt_cd`, the committee-ID field identifies the donor's own committee,
not the filer receiving the money. Scoping a query by it silently pulls
rows for the wrong entity.

**Guidance:** scope by filer through the filing relationship
(`filing_id IN (SELECT filing_id FROM filer_filings_cd WHERE filer_id = …)`),
not by the detail row's committee ID.

---

## 4. Data hygiene

### 4.1 Date columns contain corrupt future values
Some rows carry implausibly far-future dates (values beyond the current
era have been observed in contribution dates).

**Guidance:** never use an unbounded `MAX(date_column)` as a "latest
activity" without an upper bound; always bound date ranges in
plausibility windows.

### 4.2 NULL name fields are common
`payee_namf` (and other name fields) are frequently NULL. Un-guarded
concatenation (`a || b`) yields NULL and silently drops rows from
`GROUP BY`/`ORDER BY` results.

**Guidance:** always `COALESCE(field, '')` before concatenating.

### 4.3 Contribution refunds appear as expenditure lines
"Return of contribution" payments to donors are recorded in the expenditure
tables with a donor (or the donor's agent) as payee. They are not vendor
spend and inflate expense totals if unexamined.

**Guidance:** when a "vendor" total looks odd, scan the expenditure
descriptions for refund language before drawing conclusions.

### 4.4 Snapshot freshness
The database reflects the SOS export at the time of the last ETL run (daily
updates). Recent activity — especially for in-flight 2026 committees — lags
reality by days to weeks, and late-filed or amended reports can move
historical totals.

**Guidance:** state the snapshot date with any answer about "current"
totals.

---

## 5. Coverage gaps

### 5.1 Ballot-measure metadata stops in 2009
`ballot_measures_cd` contains ~109 rows covering 2000–2009 only. Recent
measures must be resolved by filer ID / committee name, not by the measure
table.

### 5.2 No election outcomes in the database
Win/loss results are not structured data: the SOS publishes them as PDF
reports only. The `election_results` table tracks discovered PDFs and the
`scrape_election_results` module downloads them, but parsing into
structured outcomes is unbuilt. Do not ask the database who won.

### 5.3 Precinct-level results are out of scope
Per project spec, even the planned PDF parsing excludes precinct-level
data.
