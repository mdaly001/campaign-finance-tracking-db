-- 0010: Fix fec_other_contributions to the real itoth record layout.
--
-- The table was created reusing the itcont (FEC_INDIV) column names, but
-- itoth.txt has a different field order. Verified empirically against
-- oth24.zip: all 18,667,435 rows have exactly 21 pipe-delimited fields:
--
--   0  committee_id          11 contributor_employer
--   1  amendment_indicator   12 contributor_occupation
--   2  report_type           13 contribution_date
--   3  election_code         14 contribution_amount
--   4  transaction_id        15 other_committee_id   (C00873893 on 18G transfers)
--   5  transaction_type_code 16 memo_code
--   6  contributor_type_code 17 memo_reference
--   7  contributor_name      18 back_reference      ('X' in ~99.99% of rows)
--   8  contributor_city      19 short_description   (free text, e.g. 'TRANSFER FROM ...')
--   9  contributor_state     20 receipt_no
--   10 contributor_zip
--
-- itoth has NO conduit_code / aggregate_year_to_date / committee_fec_id.
-- The previous mapping put committee IDs (e.g. 'C00873893') into the numeric
-- aggregate_year_to_date column, dead-lettering ~1.6M transfer rows.
--
-- The 17.06M rows already loaded under the wrong names are truncated here so
-- the table can be reloaded cleanly with FEC_OTH_COLUMNS.

BEGIN;

-- Views depend on the columns being dropped; recreate them afterwards.
DROP VIEW IF EXISTS fec.v_ca_money_flows;
DROP VIEW IF EXISTS fec.fec_other_contributions_current;

ALTER TABLE fec.fec_other_contributions
    ADD COLUMN IF NOT EXISTS report_type        text,
    ADD COLUMN IF NOT EXISTS back_reference    text,
    ADD COLUMN IF NOT EXISTS short_description text;

ALTER TABLE fec.fec_other_contributions
    DROP COLUMN IF EXISTS conduit_code,
    DROP COLUMN IF EXISTS aggregate_year_to_date,
    DROP COLUMN IF EXISTS committee_fec_id;

-- Existing rows carry mislabeled values; discard before the corrected reload.
TRUNCATE TABLE fec.fec_other_contributions;

-- Remove stale checkpoints so the corrected load is not skipped.
DELETE FROM public.load_checkpoint WHERE table_name = 'fec.fec_other_contributions';

-- Recreate the "current" (non-deleted) view with the corrected layout.
CREATE OR REPLACE VIEW fec.fec_other_contributions_current AS
SELECT
    sub_id,
    committee_id,
    amendment_indicator,
    report_type,
    election_code,
    transaction_id,
    transaction_type_code,
    contributor_type_code,
    contributor_name,
    contributor_city,
    contributor_state,
    contributor_zip,
    contributor_employer,
    contributor_occupation,
    contribution_date,
    contribution_amount,
    other_committee_id,
    memo_code,
    memo_reference,
    back_reference,
    short_description,
    receipt_no,
    two_year_transaction_period,
    is_redacted,
    deleted_at,
    src_file,
    load_ts
FROM fec.fec_other_contributions
WHERE deleted_at IS NULL;

-- Recreate the CA money-flows view (references the _current view above).
CREATE OR REPLACE VIEW fec.v_ca_money_flows AS
SELECT 'contribution'::text AS flow_type,
    'individual'::text AS source_type,
    v_ca_contributions.committee_id,
    NULL::text AS candidate_id,
    v_ca_contributions.contribution_date AS activity_date,
    v_ca_contributions.contribution_amount AS amount,
    v_ca_contributions.contributor_name,
    v_ca_contributions.contributor_state,
    v_ca_contributions.contributor_employer,
    v_ca_contributions.contributor_occupation,
    v_ca_contributions.transaction_id,
    NULL::text AS spending_type,
    NULL::text AS supported_opposed
   FROM fec.v_ca_contributions
UNION ALL
 SELECT 'contribution'::text AS flow_type,
    'committee'::text AS source_type,
    fec_other_contributions_current.committee_id,
    NULL::text AS candidate_id,
    fec_other_contributions_current.contribution_date AS activity_date,
    fec_other_contributions_current.contribution_amount AS amount,
    fec_other_contributions_current.contributor_name,
    fec_other_contributions_current.contributor_state,
    NULL::text AS contributor_employer,
    NULL::text AS contributor_occupation,
    fec_other_contributions_current.transaction_id,
    NULL::text AS spending_type,
    NULL::text AS supported_opposed
   FROM fec.fec_other_contributions_current
  WHERE fec_other_contributions_current.contributor_state = 'CA'::text
UNION ALL
 SELECT 'independent_expenditure'::text AS flow_type,
    NULL::text AS source_type,
    v_ca_targeted_ie.committee_id,
    v_ca_targeted_ie.candidate_id,
    v_ca_targeted_ie.expenditure_date AS activity_date,
    v_ca_targeted_ie.expenditure_amount AS amount,
    NULL::text AS contributor_name,
    v_ca_targeted_ie.candidate_state AS contributor_state,
    NULL::text AS contributor_employer,
    NULL::text AS contributor_occupation,
    v_ca_targeted_ie.transaction_id,
    v_ca_targeted_ie.spending_type,
    v_ca_targeted_ie.supported_opposed
   FROM fec.v_ca_targeted_ie;

COMMIT;
