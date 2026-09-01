-- 0004: Dedup views for amendment versions AND downstream filtering
--
-- PROBLEM 1: Every SOS CAL-ACCESS fact table stores amendment versions as
-- separate rows. A single logical transaction appears 2-10 times with
-- different amend_id values (0 = original, 1 = first amendment, etc.).
-- SUMming amounts across all amendments massively inflates totals.
--
-- PROBLEM 2: Expenditure records with agent_naml are DOWNSTREAM detail
-- records. When a committee pays RED7E, INC. $4M to buy TV ads, two
-- records appear:
--   1. Payee=RED7E, agent=NULL → $4M (upstream, counts as expenditure)
--   2. Payee=KNBC-TV, agent=RED7E → $1.4M (downstream detail, already counted)
-- Counting both double-counts the same money.
--
-- SOLUTION 1: Keep only the LATEST amend_id per logical record (highest
-- amend_id per filing_id+line_item group).
--
-- SOLUTION 2: Filter out downstream records (WHERE agent_naml IS NULL)
-- so only upstream payments are counted.
--
-- Row count impact:
--   expn_cd raw:        15,747,158  → deduped: ~12,000,000 (24% reduction)
--   Downstream filtered: ~3.7M records removed

-- ============================================================
-- Dedup views — one per fact table, preserves original columns
-- ============================================================

DROP VIEW IF EXISTS rcpt_cd_deduped CASCADE;
CREATE VIEW rcpt_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM rcpt_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS s497_cd_deduped CASCADE;
CREATE VIEW s497_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM s497_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS s498_cd_deduped CASCADE;
CREATE VIEW s498_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM s498_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS expn_cd_deduped CASCADE;
CREATE VIEW expn_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM expn_cd
WHERE agent_naml IS NULL  -- Exclude downstream records (agent details)
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS lexp_cd_deduped CASCADE;
CREATE VIEW lexp_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM lexp_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS s496_cd_deduped CASCADE;
CREATE VIEW s496_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM s496_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS loan_cd_deduped CASCADE;
CREATE VIEW loan_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM loan_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS debt_cd_deduped CASCADE;
CREATE VIEW debt_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM debt_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS splt_cd_deduped CASCADE;
CREATE VIEW splt_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM splt_cd
ORDER BY filing_id, line_item, amend_id DESC;

DROP VIEW IF EXISTS text_memo_cd_deduped CASCADE;
CREATE VIEW text_memo_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM text_memo_cd
ORDER BY filing_id, line_item, amend_id DESC;

-- ============================================================
-- Updated union views (use deduped sources)
-- Based on 0003_add_views.sql, updated to use *_deduped tables
-- ============================================================

DROP VIEW IF EXISTS receipts_all CASCADE;
CREATE VIEW receipts_all AS
-- Source 1: periodic reports (rcpt_cd) — now deduped
SELECT
    'rcpt_cd'    AS src,
    filing_id,
    tran_id,
    rcpt_date    AS receipt_date,
    amount,
    COALESCE(
        NULLIF(ctrib_naml, ''),
        NULLIF(cmte_id, '')
    ) AS donor_key,
    ctrib_naml   AS donor_naml,
    ctrib_namf   AS donor_namf,
    ctrib_dscr   AS ctrib_dscr,
    ''           AS purpose,
    cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM rcpt_date)::INTEGER AS cycle
FROM rcpt_cd_deduped

UNION ALL

-- Source 2: Form 497 24-hour large-contribution reports — now deduped
SELECT
    's497_cd'    AS src,
    filing_id,
    tran_id,
    ctrib_date   AS receipt_date,
    amount,
    COALESCE(
        NULLIF(enty_naml, ''),
        NULLIF(cmte_id, '')
    ) AS donor_key,
    enty_naml    AS donor_naml,
    enty_namf    AS donor_namf,
    ''           AS ctrib_dscr,
    ''           AS purpose,
    cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM ctrib_date)::INTEGER AS cycle
FROM s497_cd_deduped

UNION ALL

-- Source 3: Form 498 rapid-disclosure receipts — now deduped
SELECT
    's498_cd'    AS src,
    filing_id,
    tran_id,
    date_rcvd    AS receipt_date,
    amt_rcvd     AS amount,
    COALESCE(
        NULLIF(payor_naml, ''),
        NULLIF(cmte_id, '')
    ) AS donor_key,
    payor_naml   AS donor_naml,
    payor_namf   AS donor_namf,
    ''           AS ctrib_dscr,
    ''           AS purpose,
    cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM date_rcvd)::INTEGER AS cycle
FROM s498_cd_deduped;

DROP VIEW IF EXISTS expn_all CASCADE;
CREATE VIEW expn_all AS
-- Source 1: periodic expenditures (expn_cd) — now deduped
SELECT
    'expn_cd'    AS src,
    filing_id,
    tran_id,
    expn_date    AS expense_date,
    amount,
    COALESCE(
        NULLIF(payee_naml, ''),
        NULLIF(cmte_id, '')
    ) AS payee_key,
    payee_naml   AS payee_naml,
    payee_namf   AS payee_namf,
    expn_dscr    AS purpose,
    cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM expn_date)::INTEGER AS cycle
FROM expn_cd_deduped

UNION ALL

-- Source 2: linked expenditures (lexp_cd) — now deduped
-- NOTE: lexp_cd has no cmte_id column — use NULL placeholder
SELECT
    'lexp_cd'    AS src,
    filing_id,
    tran_id,
    expn_date    AS expense_date,
    amount,
    NULL         AS payee_key,
    payee_naml   AS payee_naml,
    payee_namf   AS payee_namf,
    expn_dscr    AS purpose,
    NULL         AS cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM expn_date)::INTEGER AS cycle
FROM lexp_cd_deduped

UNION ALL

-- Source 3: Form 496 24-hour expenditures (no payee name — structural blind spot) — now deduped
SELECT
    's496_cd'    AS src,
    filing_id,
    tran_id,
    exp_date     AS expense_date,
    amount,
    NULL         AS payee_key,
    NULL         AS payee_naml,
    NULL         AS payee_namf,
    expn_dscr    AS purpose,
    NULL         AS cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM exp_date)::INTEGER AS cycle
FROM s496_cd_deduped;
