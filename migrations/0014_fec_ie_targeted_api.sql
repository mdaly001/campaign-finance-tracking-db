-- 0014: Repoint fec.fec_ie_targeted at the authoritative OpenFEC API source.
-- The bulk CSV (independent_expenditure_2026.csv) was only ~1.2% of the real
-- Schedule E. We now load the full cycle from the API, which carries richer
-- fields (link_id, filing_form, is_notice, most_recent). Conflict key becomes
-- the API's globally-unique link_id so reloads are idempotent.

ALTER TABLE fec.fec_ie_targeted ADD COLUMN IF NOT EXISTS link_id       TEXT;
ALTER TABLE fec.fec_ie_targeted ADD COLUMN IF NOT EXISTS sub_id       TEXT;
ALTER TABLE fec.fec_ie_targeted ADD COLUMN IF NOT EXISTS filing_form  TEXT;
ALTER TABLE fec.fec_ie_targeted ADD COLUMN IF NOT EXISTS is_notice    BOOLEAN;
ALTER TABLE fec.fec_ie_targeted ADD COLUMN IF NOT EXISTS most_recent  BOOLEAN;
ALTER TABLE fec.fec_ie_targeted ADD COLUMN IF NOT EXISTS committee_name TEXT;

-- Replace the CSV-oriented conflict key with the API's unique link_id.
ALTER TABLE fec.fec_ie_targeted DROP CONSTRAINT IF EXISTS uq_fec_ie_targeted_key;
ALTER TABLE fec.fec_ie_targeted ADD CONSTRAINT uq_fec_ie_targeted_link UNIQUE (link_id);

-- The CSV data is incomplete; wipe it before the authoritative API reload.
TRUNCATE TABLE fec.fec_ie_targeted;

CREATE INDEX IF NOT EXISTS idx_fec_ie_targeted_form
    ON fec.fec_ie_targeted (filing_form);

COMMENT ON TABLE fec.fec_ie_targeted IS
    'Candidate-targeted independent expenditures (Schedule E), loaded from OpenFEC API. support_oppose: S=for, O=against. most_recent=true = current (non-superseded) version.';
