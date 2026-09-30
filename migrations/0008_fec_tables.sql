-- ============================================================================
-- Campaign Finance Disclosure Database — Schema v3 (FEC Federal Data)
-- Phase 2: Federal (FEC) campaign finance data with California focus
-- ============================================================================
--
-- Source: FEC bulk downloads (https://www.fec.gov/data/browse-data/?tab=bulk-data)
--   Verified file layouts against live 2026 bulk files (2026-09-21)
--
-- All tables in separate `fec` schema to isolate from CAL-ACCESS data.
-- Column names follow openFEC canonical names (lower_snake).
--
-- Conventions:
--   - Table names: fec.<entity> (e.g., fec.fec_individual_contributions)
--   - Money: NUMERIC(30,2)
--   - Dates: DATE (FEC uses MMDDYYYY format in bulk files)
--   - Dedup: transaction_id (20-digit unique identifier)
--   - Partitioning: by two_year_transaction_period
--   - CA lens: v_ca_* views on top of national data
--
-- ============================================================================

-- Create the fec schema
CREATE SCHEMA IF NOT EXISTS fec;

-- ============================================================================
-- Master/Dimension Tables (cycle-agnostic)
-- ============================================================================

-- Committee master (cm.txt, 15 fields)
CREATE TABLE IF NOT EXISTS fec.fec_committees (
    committee_id TEXT NOT NULL,
    committee_name TEXT,
    treasurer_name TEXT,
    street_1 TEXT,
    street_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    organization_type TEXT,
    committee_type TEXT,
    type_designation TEXT,
    connected_organization_name TEXT,
    affiliation TEXT,
    fec_election_year TEXT,
    is_ca_based BOOLEAN GENERATED ALWAYS AS (state = 'CA') STORED,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (committee_id)
);
COMMENT ON TABLE fec.fec_committees IS 'FEC committee master file (cm.txt)';

-- Candidate master (cn.txt, 15 fields)
CREATE TABLE IF NOT EXISTS fec.fec_candidates (
    candidate_id TEXT NOT NULL,
    candidate_name TEXT,
    party TEXT,
    cycle TEXT,
    office TEXT,
    state TEXT,
    district TEXT,
    incumbent_status TEXT,
    candidate_status TEXT,
    principal_committee_id TEXT,
    pcc_street_1 TEXT,
    pcc_street_2 TEXT,
    pcc_city TEXT,
    pcc_state TEXT,
    pcc_zip TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (candidate_id)
);
COMMENT ON TABLE fec.fec_candidates IS 'FEC candidate master file (cn.txt)';

-- Candidate-committee linkage (ccl.txt, 7 fields)
CREATE TABLE IF NOT EXISTS fec.fec_candidate_pac_linkage (
    committee_id TEXT NOT NULL,
    start_year TEXT,
    end_year TEXT,
    cand_id TEXT NOT NULL,
    cand_office TEXT,
    cand_state TEXT,
    cand_party TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (committee_id, cand_id, start_year)
);
COMMENT ON TABLE fec.fec_candidate_pac_linkage IS 'FEC candidate-committee linkage file (ccl.txt)';

-- ============================================================================
-- Fact Tables (partitioned by cycle)
-- ============================================================================

-- Individual contributions (itcont, 21 fields)
CREATE TABLE IF NOT EXISTS fec.fec_individual_contributions (
    sub_id BIGSERIAL,
    committee_id TEXT NOT NULL,
    amendment_indicator TEXT,
    transaction_type_code TEXT,
    other_committee_id TEXT,
    transaction_id TEXT NOT NULL,
    conduit_code TEXT,
    contributor_type_code TEXT,
    contributor_name TEXT,
    contributor_first_name TEXT,
    contributor_last_name TEXT,
    contributor_city TEXT,
    contributor_state TEXT,
    contributor_zip TEXT,
    contributor_employer TEXT,
    contributor_occupation TEXT,
    contribution_date DATE,
    contribution_amount NUMERIC(30,2),
    aggregate_year_to_date NUMERIC(30,2),
    memo_code TEXT,
    memo_reference TEXT,
    election_code TEXT,
    committee_fec_id TEXT,
    image_number TEXT,
    receipt_no TEXT,
    two_year_transaction_period SMALLINT NOT NULL,
    is_redacted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    src_file TEXT,
    load_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub_id, two_year_transaction_period)
);
COMMENT ON TABLE fec.fec_individual_contributions IS 'FEC individual contributions (itcont) — Schedule A';

-- Partition by cycle
CREATE TABLE fec.fec_individual_contributions_2024 PARTITION OF fec.fec_individual_contributions
    FOR VALUES IN (2024);
CREATE TABLE fec.fec_individual_contributions_2026 PARTITION OF fec.fec_individual_contributions
    FOR VALUES IN (2026);

-- Unique constraint on transaction_id within each cycle
CREATE UNIQUE INDEX IF NOT EXISTS uq_fec_indiv_trans_id
    ON fec.fec_individual_contributions (two_year_transaction_period, committee_id, transaction_id);

-- Other contributions (itoth, similar to itcont)
CREATE TABLE IF NOT EXISTS fec.fec_other_contributions (
    sub_id BIGSERIAL,
    committee_id TEXT NOT NULL,
    amendment_indicator TEXT,
    transaction_type_code TEXT,
    other_committee_id TEXT,
    transaction_id TEXT NOT NULL,
    conduit_code TEXT,
    contributor_type_code TEXT,
    contributor_name TEXT,
    contributor_city TEXT,
    contributor_state TEXT,
    contributor_zip TEXT,
    contribution_date DATE,
    contribution_amount NUMERIC(30,2),
    memo_code TEXT,
    memo_reference TEXT,
    election_code TEXT,
    two_year_transaction_period SMALLINT NOT NULL,
    is_redacted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    src_file TEXT,
    load_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub_id, two_year_transaction_period)
);
CREATE TABLE fec.fec_other_contributions_2024 PARTITION OF fec.fec_other_contributions
    FOR VALUES IN (2024);
CREATE TABLE fec.fec_other_contributions_2026 PARTITION OF fec.fec_other_contributions
    FOR VALUES IN (2026);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fec_oth_trans_id
    ON fec.fec_other_contributions (two_year_transaction_period, committee_id, transaction_id);

-- Conduit contributions (itpas2)
CREATE TABLE IF NOT EXISTS fec.fec_conduit_contributions (
    sub_id BIGSERIAL,
    committee_id TEXT NOT NULL,
    amendment_indicator TEXT,
    transaction_type_code TEXT,
    other_committee_id TEXT,
    transaction_id TEXT NOT NULL,
    conduit_code TEXT,
    contributor_type_code TEXT,
    contributor_name TEXT,
    contributor_city TEXT,
    contributor_state TEXT,
    contributor_zip TEXT,
    contribution_date DATE,
    contribution_amount NUMERIC(30,2),
    memo_code TEXT,
    memo_reference TEXT,
    election_code TEXT,
    two_year_transaction_period SMALLINT NOT NULL,
    is_redacted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    src_file TEXT,
    load_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub_id, two_year_transaction_period)
);
CREATE TABLE fec.fec_conduit_contributions_2024 PARTITION OF fec.fec_conduit_contributions
    FOR VALUES IN (2024);
CREATE TABLE fec.fec_conduit_contributions_2026 PARTITION OF fec.fec_conduit_contributions
    FOR VALUES IN (2026);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fec_pas2_trans_id
    ON fec.fec_conduit_contributions (two_year_transaction_period, committee_id, transaction_id);

-- Independent expenditures (oppexp)
CREATE TABLE IF NOT EXISTS fec.fec_independent_expenditures (
    sub_id BIGSERIAL,
    committee_id TEXT NOT NULL,
    amendment_indicator TEXT,
    transaction_type_code TEXT,
    transaction_id TEXT NOT NULL,
    expenditure_date DATE,
    expenditure_amount NUMERIC(30,2),
    description TEXT,
    purpose TEXT,
    candidate_id TEXT,
    candidate_name TEXT,
    candidate_office TEXT,
    candidate_state TEXT,
    candidate_district TEXT,
    spending_type TEXT,
    supported_opposed TEXT,
    memo_code TEXT,
    memo_reference TEXT,
    two_year_transaction_period SMALLINT NOT NULL,
    is_redacted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    src_file TEXT,
    load_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sub_id, two_year_transaction_period)
);
CREATE TABLE fec.fec_independent_expenditures_2024 PARTITION OF fec.fec_independent_expenditures
    FOR VALUES IN (2024);
CREATE TABLE fec.fec_independent_expenditures_2026 PARTITION OF fec.fec_independent_expenditures
    FOR VALUES IN (2026);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fec_oppexp_trans_id
    ON fec.fec_independent_expenditures (two_year_transaction_period, committee_id, transaction_id);

-- ============================================================================
-- Dedup Views (latest version per transaction_id, tombstones excluded)
-- ============================================================================

CREATE OR REPLACE VIEW fec.fec_individual_contributions_current AS
SELECT * FROM fec.fec_individual_contributions
WHERE deleted_at IS NULL;

CREATE OR REPLACE VIEW fec.fec_other_contributions_current AS
SELECT * FROM fec.fec_other_contributions
WHERE deleted_at IS NULL;

CREATE OR REPLACE VIEW fec.fec_conduit_contributions_current AS
SELECT * FROM fec.fec_conduit_contributions
WHERE deleted_at IS NULL;

CREATE OR REPLACE VIEW fec.fec_independent_expenditures_current AS
SELECT * FROM fec.fec_independent_expenditures
WHERE deleted_at IS NULL;

-- ============================================================================
-- CA Lens Views
-- ============================================================================

-- CA-based committees
CREATE OR REPLACE VIEW fec.v_ca_committees AS
SELECT * FROM fec.fec_committees
WHERE is_ca_based = TRUE OR committee_id IN (
    SELECT DISTINCT committee_id FROM fec.fec_candidate_pac_linkage
    WHERE cand_state = 'CA'
);

-- CA federal candidates
CREATE OR REPLACE VIEW fec.v_ca_candidates AS
SELECT * FROM fec.fec_candidates
WHERE state = 'CA';

-- CA individual contributions
CREATE OR REPLACE VIEW fec.v_ca_contributions AS
SELECT * FROM fec.fec_individual_contributions_current
WHERE contributor_state = 'CA';

-- CA-targeted independent expenditures
CREATE OR REPLACE VIEW fec.v_ca_targeted_ie AS
SELECT * FROM fec.fec_independent_expenditures_current
WHERE candidate_state = 'CA';

-- CA money flows (union of all CA-related federal activity)
CREATE OR REPLACE VIEW fec.v_ca_money_flows AS
SELECT 'contribution' AS flow_type, 'individual' AS source_type,
       committee_id, NULL AS candidate_id, contribution_date AS activity_date,
       contribution_amount AS amount, contributor_name, contributor_state,
       contributor_employer, contributor_occupation, transaction_id,
       NULL AS spending_type, NULL AS supported_opposed
FROM fec.v_ca_contributions
UNION ALL
SELECT 'contribution' AS flow_type, 'committee' AS source_type,
       committee_id, NULL AS candidate_id, contribution_date AS activity_date,
       contribution_amount AS amount, contributor_name, contributor_state,
       NULL, NULL, transaction_id, NULL, NULL
FROM fec.fec_other_contributions_current
WHERE contributor_state = 'CA'
UNION ALL
SELECT 'independent_expenditure' AS flow_type, NULL AS source_type,
       committee_id, candidate_id, expenditure_date AS activity_date,
       expenditure_amount AS amount, NULL, candidate_state,
       NULL, NULL, transaction_id, spending_type, supported_opposed
FROM fec.v_ca_targeted_ie;

-- ============================================================================
-- Source File Metadata
-- ============================================================================

CREATE TABLE IF NOT EXISTS fec.load_source_files (
    file_id BIGSERIAL PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    cycle SMALLINT,
    source TEXT NOT NULL DEFAULT 'fec',
    checksum TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    row_count BIGINT,
    status TEXT NOT NULL DEFAULT 'loaded',
    notes TEXT
);
COMMENT ON TABLE fec.load_source_files IS 'Tracks FEC source file loads';

-- ============================================================================
-- Cross-Jurisdiction Link Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS fec.xj_entity_links (
    link_id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    fec_key TEXT NOT NULL,
    ca_key TEXT NOT NULL,
    match_method TEXT,
    match_score NUMERIC(5,4),
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fec.xj_entity_links IS 'Cross-jurisdiction entity links (FEC ↔ CAL-ACCESS)';

CREATE TABLE IF NOT EXISTS fec.xj_donors (
    donor_id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    fec_transaction_ids TEXT[],
    ca_filer_ids TEXT[],
    match_score NUMERIC(5,4),
    status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fec.xj_donors IS 'Cross-jurisdiction donor matches';

-- ============================================================================
-- Indexes for CA Lens Performance
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_fec_indiv_contrib_state
    ON fec.fec_individual_contributions (contributor_state);
CREATE INDEX IF NOT EXISTS idx_fec_indiv_committee
    ON fec.fec_individual_contributions (committee_id);
CREATE INDEX IF NOT EXISTS idx_fec_indiv_date
    ON fec.fec_individual_contributions (contribution_date);
CREATE INDEX IF NOT EXISTS idx_fec_indiv_cycle
    ON fec.fec_individual_contributions (two_year_transaction_period);

CREATE INDEX IF NOT EXISTS idx_fec_ie_candidate_state
    ON fec.fec_independent_expenditures (candidate_state);
CREATE INDEX IF NOT EXISTS idx_fec_ie_committee
    ON fec.fec_independent_expenditures (committee_id);
CREATE INDEX IF NOT EXISTS idx_fec_ie_cycle
    ON fec.fec_independent_expenditures (two_year_transaction_period);

CREATE INDEX IF NOT EXISTS idx_fec_committees_ca
    ON fec.fec_committees (is_ca_based);
CREATE INDEX IF NOT EXISTS idx_fec_candidates_state
    ON fec.fec_candidates (state);

CREATE INDEX IF NOT EXISTS idx_fec_xj_links_status
    ON fec.xj_entity_links (status);
CREATE INDEX IF NOT EXISTS idx_fec_xj_donors_status
    ON fec.xj_donors (status);

-- ============================================================================
-- Grant access to cfdb_reader
-- ============================================================================

GRANT USAGE ON SCHEMA fec TO cfdb_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA fec TO cfdb_reader;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA fec TO cfdb_reader;
