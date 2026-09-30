-- 0012: Fix fec_individual_contributions to the real itcont record layout.
--
-- itcont.txt and itoth.txt share an IDENTICAL 21-field layout (verified
-- field-by-field against the 2024-cycle files). The previous FEC_CONTRIB_COLUMNS
-- mapping mislabeled positions 2/3/5/15/18/19:
--   [2]  was transaction_type_code -> actually report_type (M9/M10/Q3/12P)
--   [3]  was other_committee_id    -> actually election_code (P2024/G2024)
--   [5]  was conduit_code          -> actually transaction_type_code (15/15E)
--   [15] was aggregate_year_to_date-> actually other_committee_id (C00401224)
--   [18] was election_code         -> actually back_reference
--   [19] was committee_fec_id     -> actually short_description
-- There is NO aggregate_year_to_date / conduit_code / committee_fec_id in the
-- file. The numeric aggregate coercion was failing on committee IDs (non-fatal,
-- value dropped to NULL) on the ~49% of rows that are earmarks/transfers.
--
-- This mirrors migration 0010 (which did the same for itoth). The dependent
-- views are dropped and recreated with the corrected column set.

BEGIN;

-- Drop the dependent view chain (recreated below).
DROP VIEW IF EXISTS fec.v_ca_money_flows;
DROP VIEW IF EXISTS fec.v_ca_contributions;
DROP VIEW IF EXISTS fec.fec_individual_contributions_current;

-- Transform the base table to the correct receipt layout.
ALTER TABLE fec.fec_individual_contributions
    ADD COLUMN IF NOT EXISTS report_type        text,
    ADD COLUMN IF NOT EXISTS back_reference    text,
    ADD COLUMN IF NOT EXISTS short_description text;

ALTER TABLE fec.fec_individual_contributions
    DROP COLUMN IF EXISTS conduit_code,
    DROP COLUMN IF EXISTS aggregate_year_to_date,
    DROP COLUMN IF EXISTS committee_fec_id;

-- Discard mislabeled + test rows; clear checkpoint for the corrected reload.
TRUNCATE TABLE fec.fec_individual_contributions;
DELETE FROM public.load_checkpoint WHERE table_name = 'fec.fec_individual_contributions';

-- Recreate the "current" (non-deleted) view with the corrected layout.
CREATE OR REPLACE VIEW fec.fec_individual_contributions_current AS
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
    contributor_first_name,
    contributor_last_name,
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
    image_number,
    receipt_no,
    two_year_transaction_period,
    is_redacted,
    deleted_at,
    src_file,
    load_ts
FROM fec.fec_individual_contributions
WHERE deleted_at IS NULL;

-- Recreate the CA individual-contributions view.
CREATE OR REPLACE VIEW fec.v_ca_contributions AS
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
    contributor_first_name,
    contributor_last_name,
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
    image_number,
    receipt_no,
    two_year_transaction_period,
    is_redacted,
    deleted_at,
    src_file,
    load_ts
FROM fec.fec_individual_contributions_current
WHERE contributor_state = 'CA';

-- Recreate the CA money-flows view (references v_ca_contributions,
-- fec_other_contributions_current, v_ca_targeted_ie).
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
