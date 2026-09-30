-- 0015: Relax NOT NULL on file_number / transaction_id.
-- Some Schedule E records (esp. older / F3 filings) have null file_number or
-- transaction_id. The API's link_id is the authoritative unique key, so these
-- are informational only and must allow NULL.

ALTER TABLE fec.fec_ie_targeted ALTER COLUMN file_number DROP NOT NULL;
ALTER TABLE fec.fec_ie_targeted ALTER COLUMN transaction_id DROP NOT NULL;
