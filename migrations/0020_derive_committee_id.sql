-- 0020: derive the recipient/spender committee for blank-cmte_id fact rows.
--
-- WHY
-- ~~~
-- ~94% of rcpt_cd / expn_cd rows carry a BLANK cmte_id: CAL-ACCESS does not
-- store the recipient/spender on the line for a committee's own Form 460/496/
-- 497/498 filings. The authoritative committee is the one that FILED the
-- report, i.e. filing_id -> filer_filings_cd.filer_id. Without this, every
-- recipient-based query (entity_contributions / entity_expenditures / the
-- committee_* tools) silently misses the vast majority of records — a real
-- bug caught by cross-checking CAL-ACCESS (e.g. M. Quinn Delaney's $39,200
-- to Becerra for Governor 2026 was invisible to a cmte_id filter).
--
-- WHY MATERIALIZED VIEWS (not plain views, not a base-table backfill)
-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
-- * A plain view that COALESCEs the derived cmte_id cannot push the
--   `cmte_id = X` predicate below the amendment-dedup DISTINCT ON, so every
--   recipient query re-scans + hash-joins all ~13.5M unattributable rows
--   (~14s). Materializing pre-computes the derived cmte_id so the query hits
--   a plain btree index on cmte_id instead.
-- * A base-table backfill would switch ~2.6M multi-line-item rows out of the
--   dedup UNATTRIBUTABLE fallback (DISTINCT ON (filing_id, line_item)) into
--   the ATTRIBUTED branch (DISTINCT ON (cmte_id, tran_id)), COLLAPSING
--   distinct line items. We therefore derive cmte_id only in the outer
--   receipts_all / expn_all, AFTER dedup — the base tables stay a faithful
--   CAL-ACCESS mirror and the dedup row set is IDENTICAL to before; only the
--   cmte_id value of previously-blank rows is filled in.
--
-- WHY COALESCE(raw, derived) (raw wins when present)
-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
-- The ~1.2M rows that DO have a cmte_id come from consolidated / service-
-- provider filings (e.g. one F401 filing_id covering many committees) where
-- the raw cmte_id IS the explicit recipient and the filing's filer is a
-- different entity. So the raw value is authoritative when present; we only
-- fall back to the filing's filer for the blank rows.
--
-- MAINTENANCE
-- ~~~~~~~~~~~
-- filing_filer, receipts_all and expn_all are MATERIALIZED VIEWS. Refresh
-- them after any load that changes filer_filings_cd / the fact tables:
--   core.etl.committee_id.refresh_committee_views(engine)
-- REFRESH ... CONCURRENTLY is used (unique keys below) so reads are not
-- blocked during the refresh.

-- ------------------------------------------------------------------ #
-- 1. One filer per filing (MIN tie-break for the ~1,277 filing_ids that
--    are shared across multiple filers/forms; prevents fan-out in the join).
-- ------------------------------------------------------------------ #
-- Drop whatever currently exists under these names, whether a plain VIEW (the
-- pre-0020 definition) or a MATERIALIZED VIEW (a prior 0020 run), so the
-- migration is idempotent. A plain `DROP VIEW` errors on a matview and vice
-- versa, hence the type-aware DO block. Order matters: receipts_all/expn_all
-- reference filing_filer, so they are dropped FIRST (dependents before the
-- base) to avoid a CASCADE removing an object the loop then re-drops.
DO $$
DECLARE n text; k "char";
BEGIN
    FOREACH n IN ARRAY ARRAY['receipts_all', 'expn_all', 'filing_filer'] LOOP
        k := (SELECT relkind FROM pg_class WHERE oid = to_regclass(n));
        IF k = 'm' THEN
            EXECUTE format('DROP MATERIALIZED VIEW %I', n);
        ELSIF k = 'v' THEN
            EXECUTE format('DROP VIEW %I', n);
        END IF;
    END LOOP;
END $$;

CREATE MATERIALIZED VIEW filing_filer AS
SELECT filing_id, MIN(filer_id)::text AS filer_id
FROM filer_filings_cd
GROUP BY filing_id;

CREATE UNIQUE INDEX ix_filing_filer_filing_id ON filing_filer (filing_id);
CREATE INDEX ix_filing_filer_filer_id ON filing_filer (filer_id);

-- ------------------------------------------------------------------ #
-- 2. receipts_all: contributions (rcpt_cd + 24-hr s497_cd + s498_cd),
--    materialized, with cmte_id derived from the filing when the row's own
--    is blank.
--
--    CROSS-BRANCH DUPLICATE: the deduped sources keep BOTH an attributed
--    row (raw cmte_id present) and an unattributable row (raw cmte_id blank)
--    for the same (filing_id, line_item, tran_id) when a record was filed
--    with and without the committee. Deriving cmte_id on the blank row would
--    make it inherit the same committee and DOUBLE-COUNT the transaction. So
--    we collapse with DISTINCT ON (src, filing_id, line_item, tran_id),
--    preferring the attributed row (raw cmte_id present) so the explicit
--    recipient wins over the filing-derived one. line_item is in the key, so
--    distinct line items are never collapsed.
-- ------------------------------------------------------------------ #
CREATE MATERIALIZED VIEW receipts_all AS
WITH base AS (
    SELECT
        'rcpt_cd'::text              AS src,
        r.filing_id,
        r.amend_id,
        r.tran_id,
        r.line_item,
        r.rcpt_date                  AS receipt_date,
        r.amount,
        COALESCE(r.ctrib_naml, '')   AS donor_naml,
        COALESCE(r.ctrib_namf, '')   AS donor_namf,
        r.ctrib_dscr,
        COALESCE(NULLIF(r.cmte_id, ''), ff.filer_id) AS cmte_id,
        r.cmte_id                    AS raw_cmte,
        r.memo_refno,
        COALESCE(
            NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
            r.ctrib_dscr,
            COALESCE(NULLIF(r.cmte_id, ''), ff.filer_id)
        )                            AS donor_key,
        COALESCE(
            NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
            r.ctrib_dscr
        )                            AS donor_name
    FROM rcpt_cd_deduped r
    LEFT JOIN filing_filer ff ON ff.filing_id = r.filing_id
    UNION ALL
    SELECT
        's497_cd'::text              AS src,
        s.filing_id,
        s.amend_id,
        s.tran_id,
        s.line_item,
        s.ctrib_date                 AS receipt_date,
        s.amount,
        COALESCE(s.enty_naml, '')    AS donor_naml,
        COALESCE(s.enty_namf, '')    AS donor_namf,
        NULL::text                   AS ctrib_dscr,
        COALESCE(NULLIF(s.cmte_id, ''), ff.filer_id) AS cmte_id,
        s.cmte_id                    AS raw_cmte,
        s.memo_refno,
        COALESCE(
            NULLIF(TRIM(COALESCE(s.enty_naml, '') || ' ' || COALESCE(s.enty_namf, '')), ''),
            COALESCE(NULLIF(s.cmte_id, ''), ff.filer_id)
        )                            AS donor_key,
        NULLIF(TRIM(COALESCE(s.enty_naml, '') || ' ' || COALESCE(s.enty_namf, '')), '')
                                     AS donor_name
    FROM s497_cd_deduped s
    LEFT JOIN filing_filer ff ON ff.filing_id = s.filing_id
    UNION ALL
    SELECT
        's498_cd'::text              AS src,
        s.filing_id,
        s.amend_id,
        s.tran_id,
        s.line_item,
        s.date_rcvd                  AS receipt_date,
        s.amt_rcvd                   AS amount,
        COALESCE(s.payor_naml, '')   AS donor_naml,
        COALESCE(s.payor_namf, '')   AS donor_namf,
        NULL::text                   AS ctrib_dscr,
        COALESCE(NULLIF(s.cmte_id, ''), ff.filer_id) AS cmte_id,
        s.cmte_id                    AS raw_cmte,
        s.memo_refno,
        COALESCE(
            NULLIF(TRIM(COALESCE(s.payor_naml, '') || ' ' || COALESCE(s.payor_namf, '')), ''),
            COALESCE(NULLIF(s.cmte_id, ''), ff.filer_id)
        )                            AS donor_key,
        NULLIF(TRIM(COALESCE(s.payor_naml, '') || ' ' || COALESCE(s.payor_namf, '')), '')
                                     AS donor_name
    FROM s498_cd_deduped s
    LEFT JOIN filing_filer ff ON ff.filing_id = s.filing_id
    WHERE s.form_type = 'F498-R'
      AND s.amt_rcvd IS NOT NULL
)
SELECT DISTINCT ON (src, filing_id, line_item, coalesce(tran_id, ''))
       src, filing_id, amend_id, tran_id,
       coalesce(tran_id, '') AS tran_key,
       line_item, receipt_date, amount,
       donor_naml, donor_namf, ctrib_dscr, cmte_id, memo_refno, donor_key, donor_name
FROM base
ORDER BY src, filing_id, line_item, coalesce(tran_id, ''),
         (raw_cmte IS NOT NULL AND btrim(raw_cmte) <> '') DESC,
         amend_id DESC;

-- Unique key for REFRESH ... CONCURRENTLY. Postgres requires this to be on
-- plain columns (no expressions), so the coalesced tran_id is materialized as
-- the `tran_key` column above. (src, filing_id, line_item) is unique within
-- the unattributable branch; the attributed branch's few (filing_id, line_item)
-- repeats always carry a distinct tran_id, so adding tran_key makes the whole
-- thing unique.
CREATE UNIQUE INDEX ix_receipts_all_key
    ON receipts_all (src, filing_id, line_item, tran_key);
CREATE INDEX ix_receipts_all_cmte_id ON receipts_all (cmte_id);
CREATE INDEX ix_receipts_all_donor_naml ON receipts_all (donor_naml);
CREATE INDEX ix_receipts_all_date ON receipts_all (receipt_date);

-- ------------------------------------------------------------------ #
-- 3. expn_all: expenditures (expn_cd + lexp_cd + s496_cd). lexp_cd and
--    s496_cd have no cmte_id column at all, so their spender comes purely
--    from the filing. Same cross-branch dedup as receipts_all.
-- ------------------------------------------------------------------ #
CREATE MATERIALIZED VIEW expn_all AS
WITH base AS (
    SELECT
        'expn_cd'::text AS src,
        e.filing_id,
        e.tran_id,
        e.line_item,
        e.expn_date AS expense_date,
        e.amount,
        COALESCE(NULLIF(e.payee_naml, ''), NULLIF(e.cmte_id, ''), ff.filer_id) AS payee_key,
        e.payee_naml,
        e.payee_namf,
        e.expn_dscr AS purpose,
        COALESCE(NULLIF(e.cmte_id, ''), ff.filer_id) AS cmte_id,
        e.cmte_id AS raw_cmte,
        e.memo_refno,
        e.amend_id,
        EXTRACT(year FROM e.expn_date)::integer AS cycle
    FROM expn_cd_deduped e
    LEFT JOIN filing_filer ff ON ff.filing_id = e.filing_id
    UNION ALL
    SELECT
        'lexp_cd'::text AS src,
        l.filing_id,
        l.tran_id,
        l.line_item,
        l.expn_date AS expense_date,
        l.amount,
        NULL::text AS payee_key,
        l.payee_naml,
        l.payee_namf,
        l.expn_dscr AS purpose,
        ff.filer_id AS cmte_id,
        NULL::text AS raw_cmte,
        l.memo_refno,
        l.amend_id,
        EXTRACT(year FROM l.expn_date)::integer AS cycle
    FROM lexp_cd_deduped l
    LEFT JOIN filing_filer ff ON ff.filing_id = l.filing_id
    UNION ALL
    SELECT
        's496_cd'::text AS src,
        s.filing_id,
        s.tran_id,
        s.line_item,
        s.exp_date AS expense_date,
        s.amount,
        NULL::text AS payee_key,
        NULL::text AS payee_naml,
        NULL::text AS payee_namf,
        s.expn_dscr AS purpose,
        ff.filer_id AS cmte_id,
        NULL::text AS raw_cmte,
        s.memo_refno,
        s.amend_id,
        EXTRACT(year FROM s.exp_date)::integer AS cycle
    FROM s496_cd_deduped s
    LEFT JOIN filing_filer ff ON ff.filing_id = s.filing_id
)
SELECT DISTINCT ON (src, filing_id, line_item, coalesce(tran_id, ''))
       src, filing_id, tran_id,
       coalesce(tran_id, '') AS tran_key,
       line_item, expense_date, amount, payee_key,
       payee_naml, payee_namf, purpose, cmte_id, memo_refno, cycle
FROM base
ORDER BY src, filing_id, line_item, coalesce(tran_id, ''),
         (raw_cmte IS NOT NULL AND btrim(raw_cmte) <> '') DESC,
         amend_id DESC;

CREATE UNIQUE INDEX ix_expn_all_key
    ON expn_all (src, filing_id, line_item, tran_key);
CREATE INDEX ix_expn_all_cmte_id ON expn_all (cmte_id);
CREATE INDEX ix_expn_all_payee_naml ON expn_all (payee_naml);
CREATE INDEX ix_expn_all_date ON expn_all (expense_date);
