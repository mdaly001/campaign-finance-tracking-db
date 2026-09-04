-- 0005: rebuild receipts_all on the deduped sources (created by 0004) while restoring
-- every aliased column the MCP tools query, including donor_name/donor_key which the
-- 0003/0004 regenerations dropped. Idempotent (DROP + CREATE); relies on 0004's dedup
-- views, which run first in the migration order, and preserves the max-per-(filing_id,
-- line_item) dedup semantics via the deduped views' ORDER BY amend_id DESC.
DROP VIEW IF EXISTS receipts_all CASCADE;
CREATE VIEW receipts_all AS
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
FROM rcpt_cd_deduped r
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
FROM s497_cd_deduped s
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
FROM s498_cd_deduped s
WHERE s.form_type = 'F498-R'
  AND s.amt_rcvd IS NOT NULL;

-- filing_id lookups (committee-scoped tool queries) for the rapid tables;
-- rcpt_cd and filer_filings_cd already have their indexes (0001 + earlier
-- query-support work).
CREATE INDEX IF NOT EXISTS idx_s497_cd_filing_id ON s497_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_s498_cd_filing_id ON s498_cd (filing_id);
