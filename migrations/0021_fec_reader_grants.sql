-- 0021: Grant cfdb_reader SELECT on fec-schema objects created after 0008.
--
-- Migration 0008 ran `GRANT SELECT ON ALL TABLES IN SCHEMA fec`, which only
-- covers objects existing at that moment. ALTER DEFAULT PRIVILEGES was set
-- for `public` (0001) but never for `fec`, so every object created by
-- 0009-0019 was invisible to the read-only MCP role. Symptom: every fec_*
-- tool failed with "permission denied for materialized view
-- fec_ie_targeted_resolved" (and the same for fec_ie_targeted,
-- fec_ie_targeted_dedup, fec_individual_contributions_current,
-- fec_other_contributions_current, v_ca_contributions, v_ca_money_flows).
--
-- Applied live to prod (cfdb-db) and test (cfdbtest-db) on 2026-11-02;
-- this migration makes it durable across rebuilds.

GRANT SELECT ON ALL TABLES IN SCHEMA fec TO cfdb_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA fec GRANT SELECT ON TABLES TO cfdb_reader;
