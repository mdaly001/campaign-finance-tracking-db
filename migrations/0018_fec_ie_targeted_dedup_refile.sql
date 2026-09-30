-- 0018: Enhanced de-duplication of the Schedule E view.
--
-- Two independent duplication sources in FEC Schedule E:
--
--   LAYER 1 — F24 48-hour notice vs F3X periodic report of the SAME
--   expenditure. These share a transaction_id. Collapse to one row per
--   (spender_id, transaction_id), preferring the F3X actual over the F24
--   notice. (This is what 0017 already did.)
--
--   LAYER 2 — cross-filing RE-FILING. The same expenditure re-appears in
--   two DIFFERENT filings with a DIFFERENT transaction_id (e.g. a committee
--   re-reports a line in a later/amended report). most_recent=true does NOT
--   catch this, and neither does the transaction_id collapse. Mirroring the
--   California receipts_all "max rows-per-source" pattern: for each
--   (spender_id, candidate_id, expenditure_date, expenditure_amount) we keep
--   MAX(rows-per-filing) rows, preferring the amendment ('A') and the latest
--   filing. This collapses the re-filings (each filing had 1 copy -> keep 1)
--   while PRESERVING legitimate same-filing multi-line items (a filing with 2
--   identical lines -> keep 2).
--
-- Always query this view for for/against totals.

CREATE OR REPLACE VIEW fec.fec_ie_targeted_dedup AS
WITH l1 AS (
    -- Layer 1: collapse F24-notice / F3X-report of the same transaction.
    SELECT DISTINCT ON (spender_id, transaction_id) t.*
    FROM fec.fec_ie_targeted t
    WHERE t.transaction_id IS NOT NULL
    ORDER BY
        t.spender_id,
        t.transaction_id,
        CASE t.filing_form
            WHEN 'F3X' THEN 0
            WHEN 'F5'  THEN 1
            WHEN 'F24' THEN 2
            ELSE 3
        END,
        t.expenditure_amount DESC
),
base AS (
    SELECT * FROM l1
    UNION ALL
    SELECT t.* FROM fec.fec_ie_targeted t WHERE t.transaction_id IS NULL
),
per_filing AS (
    -- Layer 2a: how many copies of each logical line each filing carries.
    SELECT spender_id, candidate_id, expenditure_date, expenditure_amount,
           file_number, COUNT(*) AS filing_n
    FROM base
    GROUP BY 1, 2, 3, 4, 5
),
keep AS (
    -- Layer 2b: the most-complete filing's copy count is the truth.
    SELECT spender_id, candidate_id, expenditure_date, expenditure_amount,
           MAX(filing_n) AS keep_n
    FROM per_filing
    GROUP BY 1, 2, 3, 4
),
seq AS (
    -- Layer 2c: rank copies within each logical line, authoritative first.
    SELECT b.*,
           ROW_NUMBER() OVER (
               PARTITION BY b.spender_id, b.candidate_id,
                          b.expenditure_date, b.expenditure_amount
               ORDER BY
                   CASE b.amendment_indicator WHEN 'A' THEN 0 ELSE 1 END,
                   b.file_number DESC NULLS LAST,
                   b.transaction_id NULLS LAST
           ) AS rn
    FROM base b
)
SELECT
    s.ie_targeted_id, s.spender_id, s.spender_name, s.candidate_id, s.candidate_name,
    s.election_type, s.office_state, s.office_district, s.office, s.party_affiliation,
    s.expenditure_amount, s.expenditure_date, s.aggregate_amount, s.support_oppose,
    s.purpose, s.payee, s.file_number, s.amendment_indicator, s.transaction_id,
    s.image_number, s.receipt_date, s.election_year, s.previous_file_number,
    s.dissemination_date, s.src_file, s.load_ts, s.link_id, s.sub_id, s.filing_form,
    s.is_notice, s.most_recent, s.committee_name
FROM seq s
JOIN keep k
  ON k.spender_id           IS NOT DISTINCT FROM s.spender_id
 AND k.candidate_id         IS NOT DISTINCT FROM s.candidate_id
 AND k.expenditure_date     IS NOT DISTINCT FROM s.expenditure_date
 AND k.expenditure_amount   IS NOT DISTINCT FROM s.expenditure_amount
WHERE s.rn <= k.keep_n;

COMMENT ON VIEW fec.fec_ie_targeted_dedup IS
    'De-duplicated Schedule E: Layer 1 collapses F24-notice/F3X-report by '
    '(spender_id, transaction_id); Layer 2 collapses cross-filing re-filings by '
    '(spender_id, candidate_id, expenditure_date, expenditure_amount) keeping '
    'max rows-per-filing (amendment/latest preferred). Use for all for/against totals.';
