-- 0017: Deduplicated view over fec_ie_targeted.
-- The FEC reports each independent expenditure twice: once in the F24 48-hour
-- notice and again in the F3X periodic report (same transaction_id). Summing the
-- raw table double-counts. This view collapses each (spender_id, transaction_id)
-- to a single row, preferring the F3X actual over the F24 notice (and the larger
-- amount as a tiebreak). Records with no transaction_id can't be collapsed and
-- are passed through as-is.
--
-- ALWAYS query this view (not the base table) for accurate totals.

CREATE OR REPLACE VIEW fec.fec_ie_targeted_dedup AS
SELECT * FROM (
    SELECT DISTINCT ON (spender_id, transaction_id) t.*
    FROM fec.fec_ie_targeted t
    WHERE t.transaction_id IS NOT NULL
    ORDER BY
        t.spender_id,
        t.transaction_id,
        CASE t.filing_form
            WHEN 'F3X' THEN 0   -- periodic actual: authoritative
            WHEN 'F5'  THEN 1
            WHEN 'F24' THEN 2   -- 48-hour notice: often an estimate
            ELSE 3
        END,
        t.expenditure_amount DESC
) deduped
UNION ALL
SELECT t.* FROM fec.fec_ie_targeted t WHERE t.transaction_id IS NULL;

COMMENT ON VIEW fec.fec_ie_targeted_dedup IS
    'De-duplicated Schedule E: one row per (spender_id, transaction_id), F3X actual preferred over F24 notice. Use this for all for/against totals.';
