-- 0002: receipts_all view + query-support indexes for rapid-report tables.
--
-- The SOS CAL-ACCESS export discloses contributions in three detail tables:
--   rcpt_cd  - periodic reports (Form 460 and friends)
--   s497_cd  - Form 497 "24-hour report" large-contribution reports
--              (form_type F497P1/F497P2, rec_type S497)
--   s498_cd  - Form 498 rapid-disclosure receipts (F498-R rows; F498-A
--              attribution rows carry no separate money and are excluded)
--
-- A contribution that triggered a 24-hour report is often reported AGAIN in
-- a later periodic report, so the same gift can appear in more than one of
-- these tables. This view normalizes the three tables into a single
-- row layout; cross-source de-duplication (per donor + date + amount, keep
-- the max per-source row count) happens in the consuming query — see
-- core/mcp/tools.py.
--
-- Note: s497_cd names the donor in enty_naml/enty_namf (there is no
-- ctrib_dscr column); s498_cd names the payor in payor_naml/payor_namf.
-- The cmte_id column on receipt lines names the DONOR committee when the
-- donor is itself a committee (never the receiving one).

CREATE OR REPLACE VIEW receipts_all AS
SELECT
    'rcpt_cd'::text              AS src,
    r.filing_id,
    r.amend_id,
    r.tran_id,
    r.rcpt_date                  AS receipt_date,
    r.amount,
    COALESCE(r.ctrib_naml, '')   AS donor_naml,
    COALESCE(r.ctrib_namf, '')   AS donor_namf,
    r.ctrib_dscr,
    NULLIF(r.cmte_id, '')        AS cmte_id,
    r.memo_refno,
    COALESCE(
        NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
        r.ctrib_dscr,
        NULLIF(r.cmte_id, '')
    )                            AS donor_key,
    COALESCE(
        NULLIF(TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')), ''),
        r.ctrib_dscr
    )                            AS donor_name
FROM rcpt_cd r
UNION ALL
SELECT
    's497_cd'::text              AS src,
    s.filing_id,
    s.amend_id,
    s.tran_id,
    s.ctrib_date                 AS receipt_date,
    s.amount,
    COALESCE(s.enty_naml, '')    AS donor_naml,
    COALESCE(s.enty_namf, '')    AS donor_namf,
    NULL::text                   AS ctrib_dscr,
    NULLIF(s.cmte_id, '')        AS cmte_id,
    s.memo_refno,
    COALESCE(
        NULLIF(TRIM(COALESCE(s.enty_naml, '') || ' ' || COALESCE(s.enty_namf, '')), ''),
        NULLIF(s.cmte_id, '')
    )                            AS donor_key,
    NULLIF(TRIM(COALESCE(s.enty_naml, '') || ' ' || COALESCE(s.enty_namf, '')), '')
                                 AS donor_name
FROM s497_cd s
UNION ALL
SELECT
    's498_cd'::text              AS src,
    s.filing_id,
    s.amend_id,
    s.tran_id,
    s.date_rcvd                  AS receipt_date,
    s.amt_rcvd                   AS amount,
    COALESCE(s.payor_naml, '')   AS donor_naml,
    COALESCE(s.payor_namf, '')   AS donor_namf,
    NULL::text                   AS ctrib_dscr,
    NULLIF(s.cmte_id, '')        AS cmte_id,
    s.memo_refno,
    COALESCE(
        NULLIF(TRIM(COALESCE(s.payor_naml, '') || ' ' || COALESCE(s.payor_namf, '')), ''),
        NULLIF(s.cmte_id, '')
    )                            AS donor_key,
    NULLIF(TRIM(COALESCE(s.payor_naml, '') || ' ' || COALESCE(s.payor_namf, '')), '')
                                 AS donor_name
FROM s498_cd s
WHERE s.form_type = 'F498-R'
  AND s.amt_rcvd IS NOT NULL;

-- filing_id lookups (committee-scoped tool queries) for the rapid tables;
-- rcpt_cd and filer_filings_cd already have their indexes (0001 + earlier
-- query-support work).
CREATE INDEX IF NOT EXISTS idx_s497_cd_filing_id ON s497_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_s498_cd_filing_id ON s498_cd (filing_id);
