-- 0003: receipts_all and expn_all views + indexes for rapid-disclosure tables
--
-- The SOS CAL-ACCESS export discloses contributions and expenditures across
-- multiple detail tables. These views normalize them into a unified row layout
-- so queries and MCP tools can work against a single source.
--
-- Key conventions:
--   - cycle: derived from the transaction date year (no election-year column)
--   - donor_key: composite string for de-duplication (name or cmte_id)
--   - All dates are TIMESTAMP; dates are stored as native SQL timestamps
--   - cmte_id is VARCHAR/TEXT throughout

-- ============================================================================
-- receipts_all — normalized contributions union
-- ============================================================================

DROP VIEW IF EXISTS receipts_all;

CREATE VIEW receipts_all AS
-- Source 1: periodic reports (rcpt_cd)
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
FROM rcpt_cd

UNION ALL

-- Source 2: Form 497 24-hour large-contribution reports
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
FROM s497_cd

UNION ALL

-- Source 3: Form 498 rapid-disclosure receipts
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
FROM s498_cd;

-- ============================================================================
-- expn_all — normalized expenditures union
-- ============================================================================

DROP VIEW IF EXISTS expn_all;

CREATE VIEW expn_all AS
-- Source 1: periodic expenditures (expn_cd)
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
FROM expn_cd

UNION ALL

-- Source 2: linked expenditures (lexp_cd)
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
FROM lexp_cd

UNION ALL

-- Source 3: Form 496 24-hour expenditures (no payee name — structural blind spot)
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
FROM s496_cd;

-- ============================================================================
-- Indexes for the rapid-disclosure tables (s497, s498, s496)
-- These speed up the MCP tools' date-range and donor-name queries.
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_s497_cd_cmte_id      ON s497_cd (cmte_id);
CREATE INDEX IF NOT EXISTS idx_s497_cd_ctrib_date    ON s497_cd (ctrib_date);
CREATE INDEX IF NOT EXISTS idx_s497_cd_enty_naml     ON s497_cd (enty_naml);
CREATE INDEX IF NOT EXISTS idx_s497_cd_amount        ON s497_cd (amount);

CREATE INDEX IF NOT EXISTS idx_s498_cd_date_rcvd     ON s498_cd (date_rcvd);
CREATE INDEX IF NOT EXISTS idx_s498_cd_cmte_id       ON s498_cd (cmte_id);
CREATE INDEX IF NOT EXISTS idx_s498_cd_payor_naml    ON s498_cd (payor_naml);

CREATE INDEX IF NOT EXISTS idx_s496_cd_exp_date      ON s496_cd (exp_date);
CREATE INDEX IF NOT EXISTS idx_s496_cd_filing_id     ON s496_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_s496_cd_amount        ON s496_cd (amount);
