-- 0006: Add pg_trgm extension and GIN trigram indexes for fast vendor and donor matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_expn_cd_payee_naml_trgm
    ON expn_cd USING gin (payee_naml gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_rcpt_cd_ctrib_naml_trgm
    ON rcpt_cd USING gin (ctrib_naml gin_trgm_ops);
