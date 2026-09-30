-- 0011: Fix the systemic conflict-key bug across all FEC fact tables.
--
-- The previous unique key (two_year_transaction_period, committee_id,
-- transaction_id) is NOT unique per source row: FEC transaction_id is
-- assigned per *report*, not per line, so multi-line receipts share a
-- transaction_id. Keying on it silently collapsed ~43-66% of every fact
-- table's rows during upsert (e.g. two different contributors with the
-- same committee+transaction_id overwrote each other).
--
-- Verified against the 2024-cycle source files:
--   itcont (INDIV):  58,208,756 rows; receipt_no globally unique;
--                    (committee_id, transaction_id) 48.8% duplicate.
--   itoth  (OTH):    18,667,435 rows; receipt_no globally unique;
--                    (committee_id, transaction_id) 66.2% duplicate.
--   itpas2 (PAS2):      703,597 rows; receipt_no globally unique;
--                    (committee_id, transaction_id) 43.1% duplicate.
--   oppexp (OPPEXP):  2,249,158 rows; no receipt_no; best key
--                    (committee_id, transaction_id, transaction_unique_id)
--                    preserves 2,246,215 (99.87%; 2,943 residual last-wins).
--
-- Fix: replace the unique indexes with the correct keys, truncate the
-- collapsed data, and clear checkpoints so the tables reload faithfully.

BEGIN;

-- INDIV: (period, committee_id, receipt_no)
DROP INDEX IF EXISTS fec.uq_fec_indiv_trans_id;
CREATE UNIQUE INDEX uq_fec_indiv_receipt
    ON fec.fec_individual_contributions (two_year_transaction_period, committee_id, receipt_no);

-- OTH: (period, committee_id, receipt_no)
DROP INDEX IF EXISTS fec.uq_fec_oth_trans_id;
CREATE UNIQUE INDEX uq_fec_oth_receipt
    ON fec.fec_other_contributions (two_year_transaction_period, committee_id, receipt_no);

-- PAS2: (period, committee_id, receipt_no)
DROP INDEX IF EXISTS fec.uq_fec_pas2_trans_id;
CREATE UNIQUE INDEX uq_fec_pas2_receipt
    ON fec.fec_conduit_contributions (two_year_transaction_period, committee_id, receipt_no);

-- OPPEXP: (period, committee_id, transaction_id, transaction_unique_id)
DROP INDEX IF EXISTS fec.uq_fec_oppexp_trans_id;
CREATE UNIQUE INDEX uq_fec_oppexp_txnunique
    ON fec.fec_independent_expenditures (two_year_transaction_period, committee_id, transaction_id, transaction_unique_id);

-- Discard the collapsed rows so the corrected reload starts clean.
TRUNCATE TABLE fec.fec_individual_contributions;
TRUNCATE TABLE fec.fec_other_contributions;
TRUNCATE TABLE fec.fec_conduit_contributions;
TRUNCATE TABLE fec.fec_independent_expenditures;

-- Clear checkpoints so the corrected loads are not skipped.
DELETE FROM public.load_checkpoint WHERE table_name IN (
    'fec.fec_individual_contributions',
    'fec.fec_other_contributions',
    'fec.fec_conduit_contributions',
    'fec.fec_independent_expenditures'
);

COMMIT;
