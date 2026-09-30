-- Fix schema mismatches discovered when loading FEC_OTH / FEC_PAS2 / FEC_OPPEXP
-- Adds columns referenced by the ETL column mappings that were missing from the tables.

-- fec_other_contributions: 5 missing columns
ALTER TABLE fec.fec_other_contributions
  ADD COLUMN IF NOT EXISTS contributor_employer TEXT,
  ADD COLUMN IF NOT EXISTS contributor_occupation TEXT,
  ADD COLUMN IF NOT EXISTS aggregate_year_to_date NUMERIC(30,2),
  ADD COLUMN IF NOT EXISTS committee_fec_id TEXT,
  ADD COLUMN IF NOT EXISTS receipt_no TEXT;

-- fec_conduit_contributions: 10 missing columns
ALTER TABLE fec.fec_conduit_contributions
  ADD COLUMN IF NOT EXISTS report_type TEXT,
  ADD COLUMN IF NOT EXISTS election_year TEXT,
  ADD COLUMN IF NOT EXISTS transaction_purpose_code TEXT,
  ADD COLUMN IF NOT EXISTS contributor_employer TEXT,
  ADD COLUMN IF NOT EXISTS contributor_occupation TEXT,
  ADD COLUMN IF NOT EXISTS conduit_committee_id TEXT,
  ADD COLUMN IF NOT EXISTS conduit_candidate_id TEXT,
  ADD COLUMN IF NOT EXISTS line_number TEXT,
  ADD COLUMN IF NOT EXISTS image_number TEXT,
  ADD COLUMN IF NOT EXISTS receipt_no TEXT;

-- fec_independent_expenditures: 19 missing columns
ALTER TABLE fec.fec_independent_expenditures
  ADD COLUMN IF NOT EXISTS election_year TEXT,
  ADD COLUMN IF NOT EXISTS report_type TEXT,
  ADD COLUMN IF NOT EXISTS transaction_code TEXT,
  ADD COLUMN IF NOT EXISTS form_type TEXT,
  ADD COLUMN IF NOT EXISTS schedule_type TEXT,
  ADD COLUMN IF NOT EXISTS payee_name TEXT,
  ADD COLUMN IF NOT EXISTS payee_city TEXT,
  ADD COLUMN IF NOT EXISTS payee_state TEXT,
  ADD COLUMN IF NOT EXISTS payee_zip TEXT,
  ADD COLUMN IF NOT EXISTS election_code TEXT,
  ADD COLUMN IF NOT EXISTS category_description TEXT,
  ADD COLUMN IF NOT EXISTS loan_check_amount NUMERIC(30,2),
  ADD COLUMN IF NOT EXISTS loan_check_date DATE,
  ADD COLUMN IF NOT EXISTS payee_type TEXT,
  ADD COLUMN IF NOT EXISTS back_reference_transaction_id TEXT,
  ADD COLUMN IF NOT EXISTS image_number TEXT,
  ADD COLUMN IF NOT EXISTS transaction_unique_id TEXT,
  ADD COLUMN IF NOT EXISTS file_number TEXT,
  ADD COLUMN IF NOT EXISTS back_reference_id TEXT;

-- Remove zero-row checkpoints so real checkpoints can be written after reload
DELETE FROM public.load_checkpoint WHERE source = 'fec' AND rows_processed = 0;
