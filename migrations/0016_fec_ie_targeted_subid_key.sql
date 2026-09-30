-- 0016: Fix the unique key on fec_ie_targeted.
-- link_id is NOT unique (it links to the candidate object; many records share it).
-- The true unique record identifier is sub_id. Switch the conflict key.

ALTER TABLE fec.fec_ie_targeted DROP CONSTRAINT IF EXISTS uq_fec_ie_targeted_link;
ALTER TABLE fec.fec_ie_targeted ADD CONSTRAINT uq_fec_ie_targeted_subid UNIQUE (sub_id);
CREATE INDEX IF NOT EXISTS idx_fec_ie_targeted_link ON fec.fec_ie_targeted (link_id);

-- Wipe the collapsed data from the broken link_id-keyed load.
TRUNCATE TABLE fec.fec_ie_targeted;
