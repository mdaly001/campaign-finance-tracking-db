-- ============================================================================
-- Campaign Finance Disclosure Database — Schema v1
-- Phase 1: California State (CAL-ACCESS)
-- ============================================================================
--
-- Source: California Secretary of State, CAL-ACCESS Raw Data
-- URL: https://www.sos.ca.gov/campaign-lobbying/helpful-resources/raw-data-campaign-finance-and-lobbying-activity
-- Attribution: Informed by prior art from LA Times Newsroom (datadesk/lat-campfin-calaccess)
--
-- Schema Design Notes:
--   - All monetary columns use NUMERIC(15,2) — no FLOAT/REAL
--   - Dates use DATE or TIMESTAMPTZ (UTC)
--   - Text uses VARCHAR(n) with appropriate limits
--   - Boolean flags use BOOLEAN
--   - Large fact tables are partitioned by year (CREATE TABLE ... PARTITION BY RANGE)
--   - Entity resolution via entity/entity_alias/entity_merge_queue tables
--   - ETL infrastructure: load_checkpoint, etl_dead_letter
--
-- ERD (simplified):
--
--   [committee] 1──N [rcpt_cd] 1──N [text_memo_cd]
--                   │          │
--                   │          └─N N [cntrb_cd] (via ctrib_id)
--                   │
--                   └─N N [exppd_cd] (via payee/filer)
--                   │
--                   └─N N [loans_cd] (via cmte_id)
--                   │
--                   └─N N [inttrf_cd] (via cmte_id)
--                   │
--                   └─N N [s401_cd] (via cmte_id)
--                   │
--                   └─N N [s497_cd] (via cmte_id)
--                   │
--                   └─N N [s498_cd] (via cmte_id)
--                   │
--                   └─N N [debt_cd] (via cmte_id)
--                   │
--                   └─N N [lccm_cd] (via cmte_id)
--                   │
--                   └─N N [latt_cd] (via cmte_id)
--                   │
--                   └─N N [lpay_cd] (via cmte_id)
--                   │
--                   └─N N [smry_cd] (via filing_id)
--                   │
--                   └─N N [splts_cd] (via filing_id)
--
--   [filer] 1──N [filername_cd]
--                1──N [filer_address_cd] ──N [address_cd]
--                1──N [filer_xref_cd]
--                1──N [filer_links_cd] (self-referential)
--                1──N [filer_to_filer_type_cd] ──N [filer_types_cd]
--                1──N [filer_filings_cd] ──N [filings_cd] ──N [filing_period_cd]
--                1──N [filer_ethics_class_cd]
--                1──N [filer_interests_cd]
--                1──N [filer_acronyms_cd] ──N [acronyms_cd]
--                1──N [cvr_*] (disclosure reports)
--                1──N [lobbying_*] (lobbying disclosures)
--
--   [cvr_campaign_disclosure] N──1 [rcpt_cd] (via filing_id)
--   [cvr_lobby_disclosure]    N──1 [lemp_cd] (via filing_id)
--
--   [filing_calendar] ← used by scheduler to compute deadlines
--   [source_info]     ← tracks zip checksum, load date
--   [load_checkpoint] ← ETL checkpoint tracking
--   [etl_dead_letter] ← bad row quarantine
--   [entity]          ← resolved entity master
--   [entity_alias]    ← entity aliases for fuzzy matching
--   [entity_merge_queue] ← pending merges
--
-- Data dictionary: see docs/data_dictionary.md
-- ============================================================================

-- ============================================================================
-- 0. Extensions and schema setup
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

COMMENT ON SCHEMA public IS 'Campaign Finance Disclosure Database — Phase 1 (CA State)';

-- ============================================================================
-- 1. Reference / Dimension Tables (loaded first, referenced by fact tables)
-- ============================================================================

-- Lookups (code definitions)
CREATE TABLE lookup_codes (
    code_type VARCHAR(50) NOT NULL,
    code_id VARCHAR(50) NOT NULL,
    code_desc TEXT,
    PRIMARY KEY (code_type, code_id)
);
COMMENT ON TABLE lookup_codes IS 'CAL-ACCESS code definitions (REC_TYPE, FORM_TYPE, etc.)';

-- Acronym definitions
CREATE TABLE acronyms (
    acronym VARCHAR(20) NOT NULL PRIMARY KEY,
    stands_for TEXT NOT NULL,
    effect_dt DATE,
    a_desc TEXT
);
COMMENT ON TABLE acronyms IS 'Committee acronym definitions (CD, PC, LC, etc.)';

-- Filer type definitions
CREATE TABLE filer_types (
    filer_type VARCHAR(20) NOT NULL PRIMARY KEY,
    description TEXT,
    grp_type VARCHAR(20),
    calc_use VARCHAR(10),
    grace_period INTEGER
);
COMMENT ON TABLE filer_types IS 'Filer type definitions (PC, CD, LC, OC, etc.)';

-- Filer status types
CREATE TABLE filer_status_types (
    status_type VARCHAR(20) NOT NULL PRIMARY KEY,
    status_desc TEXT
);
COMMENT ON TABLE filer_status_types IS 'Active, Inactive, Cancelled, etc.';

-- Group type definitions
CREATE TABLE group_types (
    grp_id VARCHAR(20) NOT NULL PRIMARY KEY,
    grp_name VARCHAR(100),
    grp_desc TEXT
);
COMMENT ON TABLE group_types IS 'Committee group type definitions';

-- Report type definitions
CREATE TABLE report_types (
    rpt_id VARCHAR(20) NOT NULL PRIMARY KEY,
    rpt_name VARCHAR(100),
    rpt_desc TEXT,
    path TEXT,
    data_object TEXT,
    parms_flg_y_n BOOLEAN,
    rpt_type VARCHAR(20),
    parm_definition TEXT
);
COMMENT ON TABLE report_types IS 'CAL-ACCESS report type definitions (F496, F497, etc.)';

-- Legislative sessions
CREATE TABLE legislative_sessions (
    session_id INTEGER NOT NULL PRIMARY KEY,
    begin_date DATE NOT NULL,
    end_date DATE NOT NULL
);
COMMENT ON TABLE legislative_sessions IS 'CA legislative session years';

-- Filing period definitions
CREATE TABLE filing_periods (
    period_id VARCHAR(30) NOT NULL PRIMARY KEY,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    period_type VARCHAR(20),
    per_grp_type VARCHAR(20),
    period_desc TEXT,
    deadline DATE
);
COMMENT ON TABLE filing_periods IS 'Standard filing period definitions';

-- Filing type definitions
CREATE TABLE filing_types (
    filing_type VARCHAR(30) NOT NULL PRIMARY KEY
);
COMMENT ON TABLE filing_types IS 'Cover sheet filing type (F496, F497, etc.)';

-- ============================================================================
-- 2. Entity / Committee Tables
-- ============================================================================

-- Filers (master entity table)
CREATE TABLE filers (
    filer_id VARCHAR(20) NOT NULL PRIMARY KEY,
    -- Will be populated from filername_cd + filer_xref_cd during ETL
    source VARCHAR(20) DEFAULT 'calaccess'
);
COMMENT ON TABLE filers IS 'Master filer/committee/candidate entity registry';

CREATE INDEX idx_filers_source ON filers(source);

-- Filenames (from FILERNAME_CD)
CREATE TABLE filername (
    xref_filer_id VARCHAR(20),
    filer_id VARCHAR(20) NOT NULL,
    filer_type VARCHAR(20),
    status VARCHAR(20),
    effect_dt DATE,
    naml VARCHAR(120),
    namf VARCHAR(30),
    namt VARCHAR(40),
    nams VARCHAR(30),
    adr1 VARCHAR(80),
    adr2 VARCHAR(80),
    city VARCHAR(40),
    st CHAR(2),
    zip4 VARCHAR(10),
    phon VARCHAR(20),
    fax VARCHAR(20),
    email VARCHAR(100),
    cand_office VARCHAR(60),
    cand_dist INTEGER,
    cand_yr SMALLINT,
    cand_county VARCHAR(30),
    cand_election_type VARCHAR(30),
    cand_party VARCHAR(10),
    PRIMARY KEY (xref_filer_id, filer_id, effect_dt)
);
CREATE INDEX idx_filername_filer_id ON filername(filer_id);
CREATE INDEX idx_filername_status ON filername(status);
CREATE INDEX idx_filername_type ON filername(filer_type);
COMMENT ON TABLE filername IS 'Filer names — from FILERNAME_CD (many rows per filer_id over time)';

-- Address master (from ADDRESS_CD)
CREATE TABLE address_master (
    adrid VARCHAR(30) NOT NULL PRIMARY KEY,
    city VARCHAR(40),
    st CHAR(2),
    zip4 VARCHAR(10),
    phon VARCHAR(20),
    fax VARCHAR(20),
    email VARCHAR(100)
);
COMMENT ON TABLE address_master IS 'Address master — from ADDRESS_CD';

-- Filer ↔ Address mapping
CREATE TABLE filer_address (
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    adrid VARCHAR(30) REFERENCES address_master(adrid),
    effect_dt DATE,
    add_type VARCHAR(20),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    PRIMARY KEY (filer_id, adrid, effect_dt)
);
CREATE INDEX idx_filer_address_filer_id ON filer_address(filer_id);

-- Filer cross-references (mergers, ID changes)
CREATE TABLE filer_xref (
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    xref_id VARCHAR(20) NOT NULL,
    effect_dt DATE,
    migration_source VARCHAR(50),
    PRIMARY KEY (filer_id, xref_id, effect_dt)
);
CREATE INDEX idx_filer_xref_xref_id ON filer_xref(xref_id);
COMMENT ON TABLE filer_xref IS 'Filer ID cross-references (mergers, splits, ID changes)';

-- Filer relationships (parent/sponsor)
CREATE TABLE filer_links (
    filer_id_a VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    filer_id_b VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    active_flg BOOLEAN DEFAULT TRUE,
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    link_type VARCHAR(20),
    link_desc TEXT,
    effect_dt DATE,
    dominate_filer VARCHAR(20),
    termination_dt DATE,
    PRIMARY KEY (filer_id_a, filer_id_b, effect_dt)
);
CREATE INDEX idx_filer_links_filer_b ON filer_links(filer_id_b);
CREATE INDEX idx_filer_links_active ON filer_links(active_flg);
COMMENT ON TABLE filer_links IS 'Filer relationships (sponsorship, parent/child)';

-- Filer type assignments (many-to-many)
CREATE TABLE filer_type_assignments (
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    filer_type VARCHAR(20) NOT NULL REFERENCES filer_types(filer_type),
    active BOOLEAN DEFAULT TRUE,
    race BOOLEAN,
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    category VARCHAR(50),
    category_type VARCHAR(50),
    sub_category VARCHAR(50),
    effect_dt DATE,
    sub_category_type VARCHAR(50),
    election_type VARCHAR(30),
    sub_category_a VARCHAR(30),
    nyq_dt DATE,
    party_cd VARCHAR(10),
    county_cd VARCHAR(10),
    PRIMARY KEY (filer_id, filer_type, effect_dt)
);
CREATE INDEX idx_filer_type_assign_filer ON filer_type_assignments(filer_id);
COMMENT ON TABLE filer_type_assignments IS 'Filer-to-type assignments';

-- Filer ethics classifications
CREATE TABLE filer_ethics_class (
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    ethics_date DATE,
    PRIMARY KEY (filer_id, session_id)
);
COMMENT ON TABLE filer_ethics_class IS 'Ethics class for lobbying filers';

-- Filer interests (lobbying)
CREATE TABLE filer_interests (
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    interest_cd VARCHAR(20),
    effect_date DATE,
    PRIMARY KEY (filer_id, session_id, interest_cd)
);
COMMENT ON TABLE filer_interests IS 'Lobbying interest codes per filer';

-- Filer acronyms
CREATE TABLE filer_acronyms (
    acronym VARCHAR(20) NOT NULL REFERENCES acronyms(acronym),
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    PRIMARY KEY (acronym, filer_id)
);

-- Names master (from NAMES_CD — entity resolution target)
CREATE TABLE names_master (
    namid VARCHAR(30) NOT NULL PRIMARY KEY,
    naml VARCHAR(120),
    namf VARCHAR(30),
    namt VARCHAR(40),
    nams VARCHAR(30),
    moniker VARCHAR(30),
    moniker_pos SMALLINT,
    namm VARCHAR(30),
    fullname VARCHAR(300),
    naml_search VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_names_master_naml ON names_master(naml) WHERE naml IS NOT NULL;
CREATE INDEX idx_names_master_fullname ON names_master(fullname) WHERE fullname IS NOT NULL;
COMMENT ON TABLE names_master IS 'Entity name master — from NAMES_CD, used for fuzzy matching';

-- ============================================================================
-- 3. Filing / Report Tables
-- ============================================================================

-- Filings (cover sheets)
CREATE TABLE filings (
    filing_id VARCHAR(30) NOT NULL PRIMARY KEY,
    filing_type VARCHAR(30) REFERENCES filing_types(filing_type),
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    form_id VARCHAR(30),
    filing_date DATE,
    amend_id VARCHAR(30),
    period_id VARCHAR(30) REFERENCES filing_periods(period_id),
    election_date DATE,
    election_type VARCHAR(30),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    stmnt_type VARCHAR(20),
    stmnt_status VARCHAR(20),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    user_id VARCHAR(30),
    special_audit BOOLEAN DEFAULT FALSE,
    fine_audit BOOLEAN DEFAULT FALSE,
    rpt_start DATE,
    rpt_end DATE,
    rpt_date DATE
);
CREATE INDEX idx_filings_filer_id ON filings(filer_id);
CREATE INDEX idx_filings_filing_date ON filings(filing_date);
CREATE INDEX idx_filings_election_date ON filings(election_date);
CREATE INDEX idx_filings_filing_type ON filings(filing_type);
CREATE INDEX idx_filings_session ON filings(session_id);
COMMENT ON TABLE filings IS 'Cover sheets / filings';

-- E-filing log
CREATE TABLE efs_filing_log (
    filing_date DATE,
    filing_status VARCHAR(20),
    vendor VARCHAR(50),
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    form_type VARCHAR(30),
    error_no INTEGER,
    PRIMARY KEY (filer_id, filing_date)
);
CREATE INDEX idx_efs_log_filer_id ON efs_filing_log(filer_id);
CREATE INDEX idx_efs_log_date ON efs_filing_log(filing_date);
COMMENT ON TABLE efs_filing_log IS 'E-filing submission log';

-- Received filings tracker
CREATE TABLE received_filings (
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    filing_file_name VARCHAR(200),
    received_date DATE,
    filing_directory VARCHAR(200),
    filing_id VARCHAR(30) REFERENCES filings(filing_id),
    form_id VARCHAR(30),
    receive_comment TEXT
);
CREATE INDEX idx_received_filings_filer ON received_filings(filer_id);
CREATE INDEX idx_received_filings_filing ON received_filings(filing_id);
COMMENT ON TABLE received_filings IS 'SOS receipt tracking';

-- Header records (filing header)
CREATE TABLE hdr (
    filing_id VARCHAR(30) NOT NULL REFERENCES filings(filing_id),
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    ef_type VARCHAR(10),
    state_cd VARCHAR(10),
    cal_ver VARCHAR(10),
    soft_name VARCHAR(50),
    soft_ver VARCHAR(20),
    hdrcomment TEXT,
    PRIMARY KEY (filing_id, amend_id, rec_type)
);

-- Form header definitions
CREATE TABLE header_defs (
    line_number SMALLINT,
    form_id VARCHAR(30),
    rec_type VARCHAR(10),
    section_label VARCHAR(100),
    comments1 TEXT,
    comments2 TEXT,
    label VARCHAR(100),
    column_a VARCHAR(50),
    column_b VARCHAR(50),
    column_c VARCHAR(50),
    show_c VARCHAR(10),
    show_b VARCHAR(10),
    PRIMARY KEY (form_id, line_number, rec_type)
);
COMMENT ON TABLE header_defs IS 'Form header definitions for report parsing';

-- Image links
CREATE TABLE image_links (
    img_link_id VARCHAR(30) NOT NULL PRIMARY KEY,
    img_link_type VARCHAR(20),
    img_id VARCHAR(30),
    img_type VARCHAR(10),
    img_dt DATE
);
COMMENT ON TABLE image_links IS 'Document image links';

-- ============================================================================
-- 4. Core Fact Tables (PARTITIONED BY YEAR)
-- ============================================================================

-- RCPT_CD — Receipts (Contributions, Expenditures, Refunds)
-- PRIMARY FACT TABLE — largest single table (~3.8 GB, ~50M+ rows estimated)
CREATE TABLE rcpt_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    ctrib_naml VARCHAR(120),
    ctrib_namf VARCHAR(30),
    ctrib_namt VARCHAR(40),
    ctrib_nams VARCHAR(30),
    ctrib_city VARCHAR(40),
    ctrib_st CHAR(2),
    ctrib_zip4 VARCHAR(10),
    ctrib_emp VARCHAR(120),
    ctrib_occ VARCHAR(70),
    amount NUMERIC(15,2),
    payd_by VARCHAR(10),
    payment_description TEXT,
    receipt_dt DATE,
    disc_dtype VARCHAR(10),
    indemp VARCHAR(10),
    indocc VARCHAR(70),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    disp_first VARCHAR(30),
    disp_last VARCHAR(120),
    d_c_d_a DATE,
    d_c_d_b DATE,
    d_c_d_c DATE,
    d_c_d_d DATE,
    filer_id VARCHAR(20),
    cand_naml VARCHAR(120),
    cand_namf VARCHAR(30),
    cand_namt VARCHAR(40),
    cand_nams VARCHAR(30),
    cand_city VARCHAR(40),
    cand_st CHAR(2),
    cand_zip4 VARCHAR(10),
    committee_id VARCHAR(20),
    comm_naml VARCHAR(120),
    comm_namf VARCHAR(30),
    comm_namt VARCHAR(40),
    comm_nams VARCHAR(30),
    mail_addr VARCHAR(80),
    mail_city VARCHAR(40),
    mail_st CHAR(2),
    mail_zip4 VARCHAR(10),
    phone VARCHAR(20),
    consp_code VARCHAR(10),
    office_sought VARCHAR(60),
    office_dist INTEGER,
    election_type VARCHAR(30),
    election_date DATE,
    ballot_issue TEXT,
    jurisdiction VARCHAR(60),
    ballot_sub_jurisdiction VARCHAR(60),
    PRIMARY KEY (filing_id, amend_id, line_item)
) PARTITION BY RANGE (receipt_dt);

-- Create partition stubs for current and future years
-- These will be created dynamically during ETL as new data arrives
-- For now, create partitions for years 2018-2027
CREATE TABLE rcpt_cd_y2018 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE rcpt_cd_y2019 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE rcpt_cd_y2020 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE rcpt_cd_y2021 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE rcpt_cd_y2022 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE rcpt_cd_y2023 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE rcpt_cd_y2024 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE rcpt_cd_y2025 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE rcpt_cd_y2026 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE rcpt_cd_y2027 PARTITION OF rcpt_cd
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE INDEX idx_rcpt_cd_filing_id ON rcpt_cd(filing_id);
CREATE INDEX idx_rcpt_cd_filer_id ON rcpt_cd(filer_id);
CREATE INDEX idx_rcpt_cd_committee_id ON rcpt_cd(committee_id);
CREATE INDEX idx_rcpt_cd_receipt_dt ON rcpt_cd(receipt_dt);
CREATE INDEX idx_rcpt_cd_amount ON rcpt_cd(amount) WHERE amount > 0;
CREATE INDEX idx_rcpt_cd_cand_naml ON rcpt_cd(cand_naml) WHERE cand_naml IS NOT NULL;
CREATE INDEX idx_rcpt_cd_crib_naml ON rcpt_cd(ctrib_naml) WHERE ctrib_naml IS NOT NULL;
CREATE INDEX idx_rcpt_cd_election_date ON rcpt_cd(election_date);
CREATE INDEX idx_rcpt_cd_office_sought ON rcpt_cd(office_sought);
COMMENT ON TABLE rcpt_cd IS 'Receipts: contributions, expenditures, refunds — PARTITIONED BY receipt_dt';

-- CNTRB_CD — Contributors
CREATE TABLE cntrb_cd (
    ctrib_id VARCHAR(30) NOT NULL PRIMARY KEY,
    ctrib_naml VARCHAR(120),
    ctrib_namf VARCHAR(30),
    ctrib_namt VARCHAR(40),
    ctrib_nams VARCHAR(30),
    ctrib_city VARCHAR(40),
    ctrib_st CHAR(2),
    ctrib_zip4 VARCHAR(10),
    ctrib_emp VARCHAR(120),
    ctrib_occ VARCHAR(70),
    total_gives NUMERIC(15,2),
    total_year NUMERIC(15,2),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_cntrb_cd_filer_id ON cntrb_cd(filer_id);
CREATE INDEX idx_cntrb_cd_naml ON cntrb_cd(ctrib_naml) WHERE ctrib_naml IS NOT NULL;
COMMENT ON TABLE cntrb_cd IS 'Contributor master — aggregated contribution totals';

-- EXPN_CD — Expenditures
CREATE TABLE exppd_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    payee_naml VARCHAR(120),
    payee_namf VARCHAR(30),
    payee_namt VARCHAR(40),
    payee_nams VARCHAR(30),
    payee_city VARCHAR(40),
    payee_st CHAR(2),
    payee_zip4 VARCHAR(10),
    expn_date DATE,
    expn_dscr TEXT,
    amount NUMERIC(15,2),
    loan_id VARCHAR(30),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    payor_naml VARCHAR(120),
    payor_namf VARCHAR(30),
    payor_namt VARCHAR(40),
    payor_nams VARCHAR(30),
    payor_city VARCHAR(40),
    payor_st CHAR(2),
    payor_zip4 VARCHAR(10),
    refund_to_naml VARCHAR(120),
    refund_to_namf VARCHAR(30),
    refund_to_namt VARCHAR(40),
    refund_to_nams VARCHAR(30),
    refund_to_city VARCHAR(40),
    refund_to_st CHAR(2),
    refund_to_zip4 VARCHAR(10),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
) PARTITION BY RANGE (exppn_date);

CREATE TABLE exppd_cd_y2018 PARTITION OF exppd_cd
    FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE exppd_cd_y2019 PARTITION OF exppd_cd
    FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE exppd_cd_y2020 PARTITION OF exppd_cd
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE exppd_cd_y2021 PARTITION OF exppd_cd
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE exppd_cd_y2022 PARTITION OF exppd_cd
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE exppd_cd_y2023 PARTITION OF exppd_cd
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE exppd_cd_y2024 PARTITION OF exppd_cd
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE exppd_cd_y2025 PARTITION OF exppd_cd
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE exppd_cd_y2026 PARTITION OF exppd_cd
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE exppd_cd_y2027 PARTITION OF exppd_cd
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE INDEX idx_exppd_cd_filing_id ON exppd_cd(filing_id);
CREATE INDEX idx_exppd_cd_filer_id ON exppd_cd(filer_id);
CREATE INDEX idx_exppd_cd_payee_naml ON exppd_cd(payee_naml) WHERE payee_naml IS NOT NULL;
CREATE INDEX idx_exppd_cd_payor_naml ON exppd_cd(payor_naml) WHERE payor_naml IS NOT NULL;
CREATE INDEX idx_exppd_cd_amount ON exppd_cd(amount) WHERE amount > 0;
COMMENT ON TABLE exppd_cd IS 'Expenditures — PARTITIONED BY expn_date';

-- LOAN_CD — Loans Received/Made
CREATE TABLE loans_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    loan_type VARCHAR(10),
    entity_cd VARCHAR(10),
    lnr_naml VARCHAR(120),
    lnr_namf VARCHAR(30),
    lnr_namt VARCHAR(40),
    lnr_nams VARCHAR(30),
    loan_city VARCHAR(40),
    loan_st CHAR(2),
    loan_zip4 VARCHAR(10),
    loan_amt NUMERIC(15,2),
    loan_dt DATE,
    interest_rt NUMERIC(7,4),
    interest_yn BOOLEAN,
    repmt_amt NUMERIC(15,2),
    repmt_dt DATE,
    loan_purpose TEXT,
    balance_due NUMERIC(15,2),
    repmt_schedule TEXT,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    cmte_id VARCHAR(20),
    payor_naml VARCHAR(120),
    payor_namf VARCHAR(30),
    payor_namt VARCHAR(40),
    payor_nams VARCHAR(30),
    payor_city VARCHAR(40),
    payor_st CHAR(2),
    payor_zip4 VARCHAR(10),
    PRIMARY KEY (filing_id, amend_id, line_item)
) PARTITION BY RANGE (loan_dt);

CREATE TABLE loans_cd_y2018 PARTITION OF loans_cd
    FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE loans_cd_y2019 PARTITION OF loans_cd
    FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE loans_cd_y2020 PARTITION OF loans_cd
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE loans_cd_y2021 PARTITION OF loans_cd
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE loans_cd_y2022 PARTITION OF loans_cd
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE loans_cd_y2023 PARTITION OF loans_cd
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE loans_cd_y2024 PARTITION OF loans_cd
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE loans_cd_y2025 PARTITION OF loans_cd
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE loans_cd_y2026 PARTITION OF loans_cd
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE loans_cd_y2027 PARTITION OF loans_cd
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE INDEX idx_loans_cd_filing_id ON loans_cd(filing_id);
CREATE INDEX idx_loans_cd_cmte_id ON loans_cd(cmte_id);
CREATE INDEX idx_loans_cd_loan_dt ON loans_cd(loan_dt);
CREATE INDEX idx_loans_cd_loan_amt ON loans_cd(loan_amt) WHERE loan_amt > 0;
COMMENT ON TABLE loans_cd IS 'Loans received/made — PARTITIONED BY loan_dt';

-- INTTRF_CD — Inter-Committee Transfers
CREATE TABLE inttrf_cd (
    tran_id VARCHAR(30) NOT NULL PRIMARY KEY,
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    entitty_cd VARCHAR(10),
    tran_dt DATE,
    amount NUMERIC(15,2),
    tran_type VARCHAR(10),
    cmte_id VARCHAR(20) REFERENCES filers(filer_id),
    cmte_name VARCHAR(120),
    cmte_addr VARCHAR(80),
    cmte_city VARCHAR(40),
    cmte_st CHAR(2),
    cmte_zip4 VARCHAR(10),
    ref_dt DATE,
    ref_amt NUMERIC(15,2),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20) REFERENCES filers(filer_id)
);
CREATE INDEX idx_inttrf_cd_filing_id ON inttrf_cd(filing_id);
CREATE INDEX idx_inttrf_cd_tran_dt ON inttrf_cd(tran_dt);
CREATE INDEX idx_inttrf_cd_amount ON inttrf_cd(amount) WHERE amount > 0;
COMMENT ON TABLE inttrf_cd IS 'Inter-committee transfers';

-- DEBT_CD — Debts Owed
CREATE TABLE debt_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    payee_naml VARCHAR(120),
    payee_namf VARCHAR(30),
    payee_namt VARCHAR(40),
    payee_nams VARCHAR(30),
    payee_city VARCHAR(40),
    payee_st CHAR(2),
    payee_zip4 VARCHAR(10),
    beg_bal NUMERIC(15,2),
    debts_inc NUMERIC(15,2),
    debts_paid NUMERIC(15,2),
    end_bal NUMERIC(15,2),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);

CREATE INDEX idx_debt_cd_filing_id ON debt_cd(filing_id);
CREATE INDEX idx_debt_cd_filer_id ON debt_cd(filer_id);
CREATE INDEX idx_debt_cd_end_bal ON debt_cd(end_bal) WHERE end_bal > 0;
COMMENT ON TABLE debt_cd IS 'Debts owed';

-- ============================================================================
-- 5. Supporting Fact Tables (non-partitioned)
-- ============================================================================

-- SMRY_CD — Summary Records (per-filing totals)
CREATE TABLE smry_cd (
    filing_id VARCHAR(30) NOT NULL REFERENCES filings(filing_id),
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    amount_a NUMERIC(15,2),
    amount_b NUMERIC(15,2),
    amount_c NUMERIC(15,2),
    elec_dt DATE,
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_smry_cd_elec_dt ON smry_cd(elec_dt);
COMMENT ON TABLE smry_cd IS 'Filing summary totals';

-- SPLT_CD — Split Records
CREATE TABLE splts_cd (
    filing_id VARCHAR(30) NOT NULL REFERENCES filings(filing_id),
    amend_id VARCHAR(30),
    line_item INTEGER,
    pform_type VARCHAR(10),
    ptran_id VARCHAR(30),
    elec_date DATE,
    elec_amount NUMERIC(15,2),
    elec_code VARCHAR(10),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE splts_cd IS 'Split records (allocations across candidates/measures)';

-- TEXT_MEMO_CD — Text Memos
CREATE TABLE text_memo (
    filing_id VARCHAR(30) NOT NULL REFERENCES filings(filing_id),
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    ref_no VARCHAR(30),
    text4000 TEXT,
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE text_memo IS 'Text memo descriptions (up to 4000 chars)';

-- ============================================================================
-- 6. Disclosure Reports (CVR)
-- ============================================================================

-- CVR Campaign Disclosure (F496)
CREATE TABLE cvr_campaign_disclosure (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    entity_cd VARCHAR(10),
    filer_naml VARCHAR(120),
    filer_namf VARCHAR(30),
    filer_namt VARCHAR(40),
    filer_nams VARCHAR(30),
    report_num INTEGER,
    rpt_date DATE,
    stmt_type VARCHAR(20),
    late_rptno INTEGER,
    from_date DATE,
    thru_date DATE,
    elect_date DATE,
    cash_on_hand NUMERIC(15,2),
    total_contributions NUMERIC(15,2),
    total_expenditures NUMERIC(15,2),
    loans_received NUMERIC(15,2),
    loan_repayments NUMERIC(15,2),
    other_loans NUMERIC(15,2),
    other_payments NUMERIC(15,2),
    debts_owed NUMERIC(15,2),
    net_change NUMERIC(15,2),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    signatory_name VARCHAR(120),
    signatory_title VARCHAR(60),
    prepared_by VARCHAR(120),
    prepared_phone VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_cvr_cd_filer_id ON cvr_campaign_disclosure(filer_id);
CREATE INDEX idx_cvr_cd_rpt_date ON cvr_campaign_disclosure(rpt_date);
CREATE INDEX idx_cvr_cd_elect_date ON cvr_campaign_disclosure(elect_date);
COMMENT ON TABLE cvr_campaign_disclosure IS 'F496 Campaign Disclosure Reports';

-- CVR Registration (F400)
CREATE TABLE cvr_registration (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    sender_id VARCHAR(20),
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    entity_cd VARCHAR(10),
    filer_naml VARCHAR(120),
    filer_namf VARCHAR(30),
    filer_namt VARCHAR(40),
    filer_nams VARCHAR(30),
    report_num INTEGER,
    rpt_date DATE,
    ls_beg_yr SMALLINT,
    ls_end_yr SMALLINT,
    committee_type VARCHAR(20),
    cand_id VARCHAR(20),
    cand_name VARCHAR(120),
    cand_office VARCHAR(60),
    cand_dist INTEGER,
    cand_county VARCHAR(30),
    cand_party VARCHAR(10),
    cand_election_type VARCHAR(30),
    cand_yr SMALLINT,
    party VARCHAR(10),
    auth_name VARCHAR(120),
    auth_phone VARCHAR(20),
    auth_address VARCHAR(80),
    auth_city VARCHAR(40),
    auth_st CHAR(2),
    auth_zip4 VARCHAR(10),
    mail_addr VARCHAR(80),
    mail_city VARCHAR(40),
    mail_st CHAR(2),
    mail_zip4 VARCHAR(10),
    incrb_dt DATE,
    incrb_state VARCHAR(20),
    treas_name VARCHAR(120),
    treas_phone VARCHAR(20),
    treas_address VARCHAR(80),
    treas_city VARCHAR(40),
    treas_st CHAR(2),
    treas_zip4 VARCHAR(10),
    filing_sequence INTEGER,
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    signatory_name VARCHAR(120),
    signatory_title VARCHAR(60),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_cvr_reg_filer_id ON cvr_registration(filer_id);
COMMENT ON TABLE cvr_registration IS 'F400 Committee Registration';

-- CVR Statement of Organization (F460)
CREATE TABLE cvr_so (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    entity_cd VARCHAR(10),
    filer_naml VARCHAR(120),
    filer_namf VARCHAR(30),
    filer_namt VARCHAR(40),
    filer_nams VARCHAR(30),
    report_num INTEGER,
    rpt_date DATE,
    qual_cb BOOLEAN,
    qualfy_dt DATE,
    term_date DATE,
    term_code VARCHAR(20),
    filing_sequence INTEGER,
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_cvr_so_filer_id ON cvr_so(filer_id);
COMMENT ON TABLE cvr_so IS 'F460 Statement of Organization';

-- CVR Lobbying Disclosure (F455)
CREATE TABLE cvr_lobby_disclosure (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    sender_id VARCHAR(20),
    filer_id VARCHAR(20) REFERENCES filers(filer_id),
    entity_cd VARCHAR(10),
    filer_naml VARCHAR(120),
    filer_namf VARCHAR(30),
    filer_namt VARCHAR(40),
    filer_nams VARCHAR(30),
    report_num INTEGER,
    rpt_date DATE,
    from_date DATE,
    thru_date DATE,
    lby_orgn_naml VARCHAR(120),
    lby_orgn_namf VARCHAR(30),
    lby_orgn_namt VARCHAR(40),
    lby_orgn_nams VARCHAR(30),
    lby_orgn_adr VARCHAR(80),
    lby_orgn_city VARCHAR(40),
    lby_orgn_st CHAR(2),
    lby_orgn_zip4 VARCHAR(10),
    lby_orgn_phon VARCHAR(20),
    lby_orgn_fax VARCHAR(20),
    lby_orgn_email VARCHAR(100),
    principal_id VARCHAR(20),
    principal_name VARCHAR(120),
    principal_adr VARCHAR(80),
    principal_city VARCHAR(40),
    principal_st CHAR(2),
    principal_zip4 VARCHAR(10),
    lby_reg_id VARCHAR(30),
    lby_reg_name VARCHAR(120),
    filing_sequence INTEGER,
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_cvr_ld_filer_id ON cvr_lobby_disclosure(filer_id);
CREATE INDEX idx_cvr_ld_lby_reg_id ON cvr_lobby_disclosure(lby_reg_id);
COMMENT ON TABLE cvr_lobby_disclosure IS 'F455 Lobbying Disclosure Reports';

-- CVR2 Campaign Disclosure (Compact)
CREATE TABLE cvr2_campaign_disclosure (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    title VARCHAR(60),
    mail_city VARCHAR(40),
    mail_st CHAR(2),
    mail_zip4 VARCHAR(10),
    f460_part VARCHAR(10),
    cmte_id VARCHAR(20),
    enty_naml VARCHAR(120),
    enty_namf VARCHAR(30),
    enty_naml_search VARCHAR(200),
    enty_namf_search VARCHAR(200),
    enty_city VARCHAR(40),
    enty_st CHAR(2),
    enty_zip4 VARCHAR(10),
    enty_phone VARCHAR(20),
    enty_fax VARCHAR(20),
    enty_email VARCHAR(100),
    item_amt NUMERIC(15,2),
    item_dt DATE,
    item_desc TEXT,
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_cvr2_cd_cmte_id ON cvr2_campaign_disclosure(cmte_id);
CREATE INDEX idx_cvr2_cd_item_dt ON cvr2_campaign_disclosure(item_dt);
COMMENT ON TABLE cvr2_campaign_disclosure IS 'Compact campaign disclosure (F496 Part 2)';

-- CVR2 Lobbying Disclosure (Compact)
CREATE TABLE cvr2_lobby_disclosure (
    amend_id VARCHAR(30),
    entity_cd VARCHAR(10),
    entity_id VARCHAR(30),
    enty_namf VARCHAR(30),
    enty_naml VARCHAR(120),
    enty_nams VARCHAR(30),
    enty_namt VARCHAR(40),
    enty_title VARCHAR(60),
    filing_id VARCHAR(30),
    form_type VARCHAR(10),
    line_item INTEGER,
    rec_type VARCHAR(10),
    tran_id VARCHAR(30),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE cvr2_lobby_disclosure IS 'Compact lobbying disclosure';

-- CVR2 Registration (Compact)
CREATE TABLE cvr2_registration (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    entity_id VARCHAR(30),
    enty_naml VARCHAR(120),
    enty_namf VARCHAR(30),
    enty_namt VARCHAR(40),
    enty_nams VARCHAR(30),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE cvr2_registration IS 'Compact registration';

-- CVR2 SO (Compact)
CREATE TABLE cvr2_so (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    enty_naml VARCHAR(120),
    enty_namf VARCHAR(30),
    enty_namt VARCHAR(40),
    enty_nams VARCHAR(30),
    item_cd VARCHAR(10),
    mail_city VARCHAR(40),
    mail_st CHAR(2),
    mail_zip4 VARCHAR(10),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE cvr2_so IS 'Compact statement of organization';

-- CVR3 Verification Info
CREATE TABLE cvr3_verification_info (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    sig_date DATE,
    sig_loc VARCHAR(80),
    sig_naml VARCHAR(120),
    sig_namf VARCHAR(30),
    sig_namt VARCHAR(40),
    sig_nams VARCHAR(30),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE cvr3_verification_info IS 'E-filing verification signatures';

-- CVR E-530 (Political Candidate Statements)
CREATE TABLE cvr_e530 (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    entity_cd VARCHAR(10),
    filer_naml VARCHAR(120),
    filer_namf VARCHAR(30),
    filer_namt VARCHAR(40),
    filer_nams VARCHAR(30),
    report_num INTEGER,
    rpt_date DATE,
    filer_city VARCHAR(40),
    filer_st CHAR(2),
    filer_zip4 VARCHAR(10),
    occupation VARCHAR(70),
    employer VARCHAR(120),
    cand_id VARCHAR(20),
    cand_naml VARCHAR(120),
    cand_namf VARCHAR(30),
    cand_namt VARCHAR(40),
    cand_nams VARCHAR(30),
    cand_office VARCHAR(60),
    cand_dist INTEGER,
    cand_county VARCHAR(30),
    cand_party VARCHAR(10),
    cand_election_type VARCHAR(30),
    cand_yr SMALLINT,
    cash_on_hand NUMERIC(15,2),
    contributions NUMERIC(15,2),
    expenditures NUMERIC(15,2),
    debts_owed NUMERIC(15,2),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_cvr_e530_cand_id ON cvr_e530(cand_id);
CREATE INDEX idx_cvr_e530_cand_office ON cvr_e530(cand_office);
COMMENT ON TABLE cvr_e530 IS 'E-530 Political Candidate Statements';

-- CVR F-470 (Contribution/Expenditure Schedule)
CREATE TABLE cvr_f470 (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    filer_id VARCHAR(20),
    entity_cd VARCHAR(10),
    filer_naml VARCHAR(120),
    filer_namf VARCHAR(30),
    filer_namt VARCHAR(40),
    filer_nams VARCHAR(30),
    report_num INTEGER,
    rpt_date DATE,
    cand_city VARCHAR(40),
    cand_st CHAR(2),
    cand_zip4 VARCHAR(10),
    occupation VARCHAR(70),
    employer VARCHAR(120),
    cand_office VARCHAR(60),
    cand_dist INTEGER,
    cand_county VARCHAR(30),
    cand_party VARCHAR(10),
    cand_election_type VARCHAR(30),
    cand_yr SMALLINT,
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_cvr_f470_filer_id ON cvr_f470(filer_id);
COMMENT ON TABLE cvr_f470 IS 'F-470 Contribution/Expenditure Schedule';

-- ============================================================================
-- 7. Schedule Tables (Form-Specific)
-- ============================================================================

-- S401_CD — Schedule S401 (Independent Expenditures)
CREATE TABLE s401_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    agent_naml VARCHAR(120),
    agent_namf VARCHAR(30),
    agent_namt VARCHAR(40),
    agent_nams VARCHAR(30),
    payee_naml VARCHAR(120),
    payee_namf VARCHAR(30),
    payee_namt VARCHAR(40),
    payee_nams VARCHAR(30),
    payee_city VARCHAR(40),
    payee_st CHAR(2),
    payee_zip4 VARCHAR(10),
    expn_date DATE,
    expn_amt NUMERIC(15,2),
    expn_dscr TEXT,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    cmte_id VARCHAR(20),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_s401_filer_id ON s401_cd(filer_id);
CREATE INDEX idx_s401_cmte_id ON s401_cd(cmte_id);
CREATE INDEX idx_s401_expn_date ON s401_cd(expn_date);
CREATE INDEX idx_s401_expn_amt ON s401_cd(expn_amt) WHERE expn_amt > 0;
COMMENT ON TABLE s401_cd IS 'Schedule S401 — Independent Expenditures';

-- S496_CD — Schedule S496 (Small Contributions/Expenditures)
CREATE TABLE s496_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    amount NUMERIC(15,2),
    exp_date DATE,
    expn_dscr TEXT,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    date_thru DATE,
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_s496_exp_date ON s496_cd(exp_date);
COMMENT ON TABLE s496_cd IS 'Schedule S496 — Small Contributions/Expenditures';

-- S497_CD — Schedule S497 (Large Contributions)
CREATE TABLE s497_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    enty_naml VARCHAR(120),
    enty_namf VARCHAR(30),
    enty_namt VARCHAR(40),
    enty_nams VARCHAR(30),
    enty_city VARCHAR(40),
    enty_st CHAR(2),
    enty_zip4 VARCHAR(10),
    ctrib_emp VARCHAR(120),
    ctrib_occ VARCHAR(70),
    amount NUMERIC(15,2),
    receipt_dt DATE,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_s497_filer_id ON s497_cd(filer_id);
CREATE INDEX idx_s497_receipt_dt ON s497_cd(receipt_dt);
CREATE INDEX idx_s497_amount ON s497_cd(amount) WHERE amount > 1000;
COMMENT ON TABLE s497_cd IS 'Schedule S497 — Large Contributions (> $1,000)';

-- S498_CD — Schedule S498 (Large Expenditures)
CREATE TABLE s498_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    cmte_id VARCHAR(20),
    payor_naml VARCHAR(120),
    payor_namf VARCHAR(30),
    payor_namt VARCHAR(40),
    payor_nams VARCHAR(30),
    payor_city VARCHAR(40),
    payor_st CHAR(2),
    payor_zip4 VARCHAR(10),
    expn_date DATE,
    expn_amt NUMERIC(15,2),
    expn_dscr TEXT,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_s498_filer_id ON s498_cd(filer_id);
CREATE INDEX idx_s498_cmte_id ON s498_cd(cmte_id);
CREATE INDEX idx_s498_expn_date ON s498_cd(expn_date);
CREATE INDEX idx_s498_expn_amt ON s498_cd(expn_amt) WHERE expn_amt > 10000;
COMMENT ON TABLE s498_cd IS 'Schedule S498 — Large Expenditures (> $10,000)';

-- F-495 Part 2 (Candidate Contributions)
CREATE TABLE f495p2 (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    elect_date DATE,
    electjuris VARCHAR(30),
    contribamt NUMERIC(15,2),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_f495p2_elect_date ON f495p2(elect_date);
COMMENT ON TABLE f495p2 IS 'F-495 Part 2 — Candidate Contributions';

-- F-501/F-502 (Report of Organization/Candidate)
CREATE TABLE f501_502 (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    filer_id VARCHAR(20),
    committee_id VARCHAR(20),
    entity_cd VARCHAR(10),
    report_num INTEGER,
    rpt_date DATE,
    stmt_type VARCHAR(20),
    from_date DATE,
    thru_date DATE,
    elect_date DATE,
    cand_naml VARCHAR(120),
    cand_namf VARCHAR(30),
    cand_namt VARCHAR(40),
    cand_nams VARCHAR(30),
    cand_city VARCHAR(40),
    cand_st CHAR(2),
    cand_zip4 VARCHAR(10),
    cand_office VARCHAR(60),
    cand_dist INTEGER,
    cand_county VARCHAR(30),
    cand_party VARCHAR(10),
    cand_election_type VARCHAR(30),
    cand_yr SMALLINT,
    party VARCHAR(10),
    treas_naml VARCHAR(120),
    treas_namf VARCHAR(30),
    treas_namt VARCHAR(40),
    treas_nams VARCHAR(30),
    treas_city VARCHAR(40),
    treas_st CHAR(2),
    treas_zip4 VARCHAR(10),
    treas_phone VARCHAR(20),
    incrb_dt DATE,
    incrb_state VARCHAR(20),
    auth_naml VARCHAR(120),
    auth_namf VARCHAR(30),
    auth_namt VARCHAR(40),
    auth_nams VARCHAR(30),
    auth_city VARCHAR(40),
    auth_st CHAR(2),
    auth_zip4 VARCHAR(10),
    auth_phone VARCHAR(20),
    coverage_type VARCHAR(20),
    filing_status VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_f501_502_filer_id ON f501_502(filer_id);
CREATE INDEX idx_f501_502_cand_office ON f501_502(cand_office);
COMMENT ON TABLE f501_502 IS 'F-501/F-502 Report of Organization/Candidate';

-- F-690 Part 2 (Lobbying Amendments)
CREATE TABLE f690p2 (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    exec_date DATE,
    from_date DATE,
    thru_date DATE,
    chg_parts TEXT,
    chg_sects TEXT,
    amend_txt1 TEXT,
    PRIMARY KEY (filing_id, amend_id, line_item)
);
COMMENT ON TABLE f690p2 IS 'F-690 Part 2 — Lobbying Amendments';

-- ============================================================================
-- 8. Expenditure & Payment Tables
-- ============================================================================

-- LATT_CD — Late-Attest Payments
CREATE TABLE latt_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    recip_naml VARCHAR(120),
    recip_namf VARCHAR(30),
    recip_namt VARCHAR(40),
    recip_nams VARCHAR(30),
    recip_city VARCHAR(40),
    recip_st CHAR(2),
    recip_zip4 VARCHAR(10),
    pmt_date DATE,
    pmt_amt NUMERIC(15,2),
    pmt_type VARCHAR(10),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_latt_filer_id ON latt_cd(filer_id);
CREATE INDEX idx_latt_pmt_date ON latt_cd(pmt_date);
COMMENT ON TABLE latt_cd IS 'Late-Attest Payments';

-- LPAY_CD — Loan Payments
CREATE TABLE lpay_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    emplr_naml VARCHAR(120),
    emplr_namf VARCHAR(30),
    emplr_namt VARCHAR(40),
    emplr_nams VARCHAR(30),
    emplr_city VARCHAR(40),
    emplr_st CHAR(2),
    emplr_zip4 VARCHAR(10),
    emplr_phon VARCHAR(20),
    loan_id VARCHAR(30),
    repmt_amt NUMERIC(15,2),
    repmt_dt DATE,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_lpay_filer_id ON lpay_cd(filer_id);
CREATE INDEX idx_lpay_loan_id ON lpay_cd(loan_id);
CREATE INDEX idx_lpay_repmt_dt ON lpay_cd(repmt_dt);
COMMENT ON TABLE lpay_cd IS 'Loan Payments';

-- LCCM_CD — Campaign Committee Memo Payments
CREATE TABLE lccm_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    entity_cd VARCHAR(10),
    recip_naml VARCHAR(120),
    recip_namf VARCHAR(30),
    recip_namt VARCHAR(40),
    recip_nams VARCHAR(30),
    recip_city VARCHAR(40),
    recip_st CHAR(2),
    recip_zip4 VARCHAR(10),
    recip_id VARCHAR(30),
    pmt_date DATE,
    pmt_amt NUMERIC(15,2),
    pmt_type VARCHAR(10),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_lccm_filer_id ON lccm_cd(filer_id);
CREATE INDEX idx_lccm_recip_id ON lccm_cd(recip_id);
COMMENT ON TABLE lccm_cd IS 'Campaign Committee Memo Payments';

-- LEXP_CD — Lobbying Expenditures
CREATE TABLE lexp_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    recsubtype VARCHAR(10),
    entity_cd VARCHAR(10),
    payee_naml VARCHAR(120),
    payee_namf VARCHAR(30),
    payee_namt VARCHAR(40),
    payee_nams VARCHAR(30),
    payee_city VARCHAR(40),
    payee_st CHAR(2),
    payee_zip4 VARCHAR(10),
    payee_phon VARCHAR(20),
    payee_fax VARCHAR(20),
    payee_email VARCHAR(100),
    expn_date DATE,
    expn_amt NUMERIC(15,2),
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_lexp_filer_id ON lexp_cd(filer_id);
CREATE INDEX idx_lexp_expn_date ON lexp_cd(expn_date);
COMMENT ON TABLE lexp_cd IS 'Lobbying Expenditures';

-- LOTH_CD — Lobbyist Other Transactions
CREATE TABLE loth_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    tran_id VARCHAR(30),
    firm_name VARCHAR(120),
    firm_city VARCHAR(40),
    firm_st CHAR(2),
    firm_zip4 VARCHAR(10),
    firm_phon VARCHAR(20),
    subj_naml VARCHAR(120),
    subj_namf VARCHAR(30),
    subj_namt VARCHAR(40),
    subj_nams VARCHAR(30),
    subj_city VARCHAR(40),
    subj_st CHAR(2),
    subj_zip4 VARCHAR(10),
    subj_phon VARCHAR(20),
    subj_fax VARCHAR(20),
    subj_email VARCHAR(100),
    amount NUMERIC(15,2),
    actv_dt DATE,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_loth_filer_id ON loth_cd(filer_id);
CREATE INDEX idx_loth_actv_dt ON loth_cd(actv_dt);
COMMENT ON TABLE loth_cd IS 'Lobbyist Other Transactions';

-- ============================================================================
-- 9. Lobbying Tables
-- ============================================================================

-- LEMP_CD — Lobbyist Employment/Activities
CREATE TABLE lemp_cd (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    line_item INTEGER,
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    client_id VARCHAR(20),
    cli_naml VARCHAR(120),
    cli_namf VARCHAR(30),
    cli_namt VARCHAR(40),
    cli_nams VARCHAR(30),
    cli_city VARCHAR(40),
    cli_st CHAR(2),
    cli_zip4 VARCHAR(10),
    cli_phon VARCHAR(20),
    eff_date DATE,
    termination_dt DATE,
    lby_reg_id VARCHAR(30),
    lby_reg_name VARCHAR(120),
    lby_firm_id VARCHAR(20),
    lby_firm_name VARCHAR(120),
    lby_firm_city VARCHAR(40),
    lby_firm_st CHAR(2),
    lby_firm_zip4 VARCHAR(10),
    lby_firm_phon VARCHAR(20),
    lby_firm_fax VARCHAR(20),
    lby_firm_email VARCHAR(100),
    activities_desc TEXT,
    memo_code VARCHAR(10),
    memo_refno VARCHAR(30),
    filer_id VARCHAR(20),
    PRIMARY KEY (filing_id, amend_id, line_item)
);
CREATE INDEX idx_lemp_filer_id ON lemp_cd(filer_id);
CREATE INDEX idx_lemp_lby_reg_id ON lemp_cd(lby_reg_id);
CREATE INDEX idx_lemp_lby_firm_id ON lemp_cd(lby_firm_id);
CREATE INDEX idx_lemp_eff_date ON lemp_cd(eff_date);
COMMENT ON TABLE lemp_cd IS 'Lobbyist Employment/Activities';

-- LOBBY_AMENDMENTS_CD — Lobbying Amendment Log
CREATE TABLE lobby_amendments (
    filing_id VARCHAR(30) NOT NULL,
    amend_id VARCHAR(30),
    rec_type VARCHAR(10),
    form_type VARCHAR(10),
    exec_date DATE,
    from_date DATE,
    thru_date DATE,
    add_l_cb VARCHAR(5),
    add_l_eff VARCHAR(10),
    a_l_naml VARCHAR(120),
    a_l_namf VARCHAR(30),
    a_l_namt VARCHAR(40),
    a_l_nams VARCHAR(30),
    del_l_cb VARCHAR(5),
    del_l_eff VARCHAR(10),
    d_l_naml VARCHAR(120),
    d_l_namf VARCHAR(30),
    d_l_namt VARCHAR(40),
    d_l_nams VARCHAR(30),
    mod_l_cb VARCHAR(5),
    mod_l_eff VARCHAR(10),
    m_l_naml VARCHAR(120),
    m_l_namf VARCHAR(30),
    m_l_namt VARCHAR(40),
    m_l_nams VARCHAR(30),
    amend_desc TEXT,
    filer_id VARCHAR(20),
    principal_id VARCHAR(20),
    principal_name VARCHAR(120),
    PRIMARY KEY (filing_id, amend_id, rec_type)
);
CREATE INDEX idx_lobby_amend_filer_id ON lobby_amendments(filer_id);
COMMENT ON TABLE lobby_amendments IS 'Lobbying amendment log';

-- LOBBYING_CHG_LOG_CD — Lobbying Change Log
CREATE TABLE lobbying_chg_log (
    filer_id VARCHAR(20) NOT NULL,
    change_no INTEGER NOT NULL,
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    log_dt TIMESTAMPTZ,
    filer_type VARCHAR(20),
    correction_flg BOOLEAN,
    action VARCHAR(30),
    attribute_changed VARCHAR(60),
    ethics_dt DATE,
    interests TEXT,
    filer_full_name VARCHAR(120),
    filer_city VARCHAR(40),
    filer_st CHAR(2),
    filer_zip VARCHAR(10),
    filer_phone VARCHAR(20),
    PRIMARY KEY (filer_id, change_no)
);
CREATE INDEX idx_lobby_chg_log_filer_id ON lobbying_chg_log(filer_id);
CREATE INDEX idx_lobby_chg_log_session ON lobbying_chg_log(session_id);
COMMENT ON TABLE lobbying_chg_log IS 'Lobbying change history log';

-- LOBBYIST_CONTRIBUTIONS tables (3 periods — merged into one)
CREATE TABLE lobbyist_contributions (
    filer_id VARCHAR(20) NOT NULL,
    filing_period_start_dt DATE,
    filing_period_end_dt DATE,
    contribution_dt DATE,
    recipient_name VARCHAR(120),
    recipient_id VARCHAR(30),
    amount NUMERIC(15,2),
    source_period VARCHAR(10), -- '1', '2', or '3'
    PRIMARY KEY (filer_id, contribution_dt, recipient_id, source_period)
);
CREATE INDEX idx_lob_contrib_filer ON lobbyist_contributions(filer_id);
CREATE INDEX idx_lob_contrib_recipient ON lobbyist_contributions(recipient_id);
CREATE INDEX idx_lob_contrib_period ON lobbyist_contributions(filing_period_start_dt);
COMMENT ON TABLE lobbyist_contributions IS 'Lobbyist contributions (all periods merged)';

-- LOBBYIST_EMPLOYER tables (merged)
CREATE TABLE lobbyist_employers (
    employer_id VARCHAR(30) NOT NULL,
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    employer_name VARCHAR(120),
    current_qtr_amt NUMERIC(15,2),
    session_total_amt NUMERIC(15,2),
    contributor_id VARCHAR(30),
    interest_cd VARCHAR(20),
    interest_name VARCHAR(120),
    session_yr_1 NUMERIC(15,2),
    session_yr_2 NUMERIC(15,2),
    yr_1_ytd_amt NUMERIC(15,2),
    yr_2_ytd_amt NUMERIC(15,2),
    qtr_1 NUMERIC(15,2),
    qtr_2 NUMERIC(15,2),
    qtr_3 NUMERIC(15,2),
    qtr_4 NUMERIC(15,2),
    qtr_5 NUMERIC(15,2),
    yr_1_amt NUMERIC(15,2),
    yr_2_amt NUMERIC(15,2),
    total_amt NUMERIC(15,2),
    termination_dt DATE,
    PRIMARY KEY (employer_id)
);
CREATE INDEX idx_lob_emp_session ON lobbyist_employers(session_id);
COMMENT ON TABLE lobbyist_employers IS 'Lobbyist employer records';

-- LOBBYIST_EMPLOYER_FIRMS tables (merged)
CREATE TABLE lobbyist_employer_firms (
    employer_id VARCHAR(30) NOT NULL,
    firm_id VARCHAR(20) NOT NULL,
    firm_name VARCHAR(120),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    termination_dt DATE,
    PRIMARY KEY (employer_id, firm_id)
);
CREATE INDEX idx_lob_emp_firm ON lobbyist_employer_firms(firm_id);
COMMENT ON TABLE lobbyist_employer_firms IS 'Lobbyist employer-firm relationships';

-- LOBBYIST_EMP_LOBBYIST tables (merged)
CREATE TABLE lobbyist_employer_lobbyist (
    lobbyist_id VARCHAR(20) NOT NULL,
    employer_id VARCHAR(30) NOT NULL,
    lobbyist_last_name VARCHAR(60),
    lobbyist_first_name VARCHAR(60),
    employer_name VARCHAR(120),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    PRIMARY KEY (lobbyist_id, employer_id)
);
CREATE INDEX idx_lob_emp_lob_employer ON lobbyist_employer_lobbyist(employer_id);
COMMENT ON TABLE lobbyist_employer_lobbyist IS 'Lobbyist-employer relationships';

-- LOBBYIST_FIRM tables (merged)
CREATE TABLE lobbyist_firms (
    firm_id VARCHAR(20) NOT NULL PRIMARY KEY,
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    firm_name VARCHAR(120),
    current_qtr_amt NUMERIC(15,2),
    session_total_amt NUMERIC(15,2),
    contributor_id VARCHAR(30),
    session_yr_1 NUMERIC(15,2),
    session_yr_2 NUMERIC(15,2),
    yr_1_ytd_amt NUMERIC(15,2),
    yr_2_ytd_amt NUMERIC(15,2),
    qtr_1 NUMERIC(15,2),
    qtr_2 NUMERIC(15,2),
    qtr_3 NUMERIC(15,2),
    qtr_4 NUMERIC(15,2),
    qtr_5 NUMERIC(15,2),
    yr_1_amt NUMERIC(15,2),
    yr_2_amt NUMERIC(15,2),
    total_amt NUMERIC(15,2)
);
CREATE INDEX idx_lob_firm_session ON lobbyist_firms(session_id);
COMMENT ON TABLE lobbyist_firms IS 'Lobbyist firm records';

-- LOBBYIST_FIRM_EMPLOYER tables (merged)
CREATE TABLE lobbyist_firm_employer (
    firm_id VARCHAR(20) NOT NULL,
    filing_id VARCHAR(30) NOT NULL,
    filing_sequence INTEGER,
    firm_name VARCHAR(120),
    employer_name VARCHAR(120),
    rpt_start DATE,
    rpt_end DATE,
    per_total NUMERIC(15,2),
    cum_total NUMERIC(15,2),
    lby_actvty TEXT,
    ext_lby_actvty TEXT,
    PRIMARY KEY (firm_id, filing_id)
);
COMMENT ON TABLE lobbyist_firm_employer IS 'Lobbyist firm-employer relationships';

-- LOBBYIST_FIRM_LOBBYIST tables (merged)
CREATE TABLE lobbyist_firm_lobbyist (
    lobbyist_id VARCHAR(20) NOT NULL,
    firm_id VARCHAR(20) NOT NULL,
    lobbyist_last_name VARCHAR(60),
    lobbyist_first_name VARCHAR(60),
    firm_name VARCHAR(120),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    PRIMARY KEY (lobbyist_id, firm_id)
);
CREATE INDEX idx_lob_firm_lob_firm ON lobbyist_firm_lobbyist(firm_id);
COMMENT ON TABLE lobbyist_firm_lobbyist IS 'Lobbyist-firm relationships';

-- ============================================================================
-- 10. Ballot Measure Tables
-- ============================================================================

CREATE TABLE ballot_measures (
    election_date DATE NOT NULL,
    filer_id VARCHAR(20),
    measure_no VARCHAR(20) NOT NULL,
    measure_name TEXT NOT NULL,
    measure_short_name VARCHAR(200),
    jurisdiction VARCHAR(60),
    PRIMARY KEY (election_date, measure_no)
);
CREATE INDEX idx_ballot_msr_election ON ballot_measures(election_date);
CREATE INDEX idx_ballot_msr_jurisdiction ON ballot_measures(jurisdiction);
COMMENT ON TABLE ballot_measures IS 'Ballot measure metadata';

-- ============================================================================
-- 11. Filer Filings History
-- ============================================================================

CREATE TABLE filer_filings (
    filer_id VARCHAR(20) NOT NULL REFERENCES filers(filer_id),
    filing_id VARCHAR(30) NOT NULL REFERENCES filings(filing_id),
    period_id VARCHAR(30) REFERENCES filing_periods(period_id),
    form_id VARCHAR(30),
    filing_sequence INTEGER,
    filing_date DATE,
    stmnt_type VARCHAR(20),
    stmnt_status VARCHAR(20),
    session_id INTEGER REFERENCES legislative_sessions(session_id),
    user_id VARCHAR(30),
    special_audit BOOLEAN DEFAULT FALSE,
    fine_audit BOOLEAN DEFAULT FALSE,
    rpt_start DATE,
    rpt_end DATE,
    rpt_date DATE,
    PRIMARY KEY (filer_id, filing_id)
);
CREATE INDEX idx_filer_filings_filing_id ON filer_filings(filing_id);
CREATE INDEX idx_filer_filings_date ON filer_filings(filing_date);
CREATE INDEX idx_filer_filings_session ON filer_filings(session_id);
COMMENT ON TABLE filer_filings IS 'Filer filing history';

-- ============================================================================
-- 12. Filing Calendar
-- ============================================================================

CREATE TABLE filing_calendar (
    election_date DATE NOT NULL,
    election_type VARCHAR(30) NOT NULL,
    filing_type VARCHAR(30),
    deadline DATE NOT NULL,
    grace_period_days INTEGER DEFAULT 0,
    extended_deadline DATE,
    source VARCHAR(50), -- 'sos', 'statute', 'computed'
    notes TEXT,
    PRIMARY KEY (election_date, filing_type)
);
CREATE INDEX idx_filing_cal_election ON filing_calendar(election_date);
CREATE INDEX idx_filing_cal_deadline ON filing_calendar(deadline);
COMMENT ON TABLE filing_calendar IS 'Election dates and filing deadlines';

-- ============================================================================
-- 13. Entity Resolution Tables
-- ============================================================================

CREATE TABLE entity (
    entity_id BIGSERIAL NOT NULL PRIMARY KEY,
    naml VARCHAR(120) NOT NULL,
    namf VARCHAR(30),
    namt VARCHAR(40),
    nams VARCHAR(30),
    moniker VARCHAR(30),
    namm VARCHAR(30),
    fullname VARCHAR(300),
    entity_type VARCHAR(20) DEFAULT 'unknown', -- 'person', 'committee', 'candidate', 'firm'
    source_filer_id VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_by BIGINT REFERENCES entity(entity_id) -- self-ref: merged into this entity
);
CREATE INDEX idx_entity_naml ON entity(naml) WHERE naml IS NOT NULL;
CREATE INDEX idx_entity_fullname ON entity(fullname) WHERE fullname IS NOT NULL;
CREATE INDEX idx_entity_type ON entity(entity_type);
COMMENT ON TABLE entity IS 'Resolved entity master';

CREATE TABLE entity_alias (
    alias_id BIGSERIAL NOT NULL PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
    alias_name VARCHAR(300) NOT NULL,
    source_filer_id VARCHAR(20),
    source_table VARCHAR(30), -- e.g., 'filername', 'cntrb_cd', 'names_master'
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_entity_alias_entity ON entity_alias(entity_id);
CREATE INDEX idx_entity_alias_name ON entity_alias(alias_name) WHERE alias_name IS NOT NULL;
COMMENT ON TABLE entity_alias IS 'Entity aliases for fuzzy matching';

CREATE TABLE entity_merge_queue (
    queue_id BIGSERIAL NOT NULL PRIMARY KEY,
    entity_a_id BIGINT NOT NULL REFERENCES entity(entity_id),
    entity_b_id BIGINT NOT NULL REFERENCES entity(entity_id),
    match_score NUMERIC(5,4), -- 0.0 to 1.0
    match_method VARCHAR(30), -- 'trigram', 'soundex', 'levenshtein'
    status VARCHAR(20) DEFAULT 'pending', -- pending, accepted, rejected
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_entity_merge_status ON entity_merge_queue(status);
CREATE INDEX idx_entity_merge_entity_a ON entity_merge_queue(entity_a_id);
CREATE INDEX idx_entity_merge_entity_b ON entity_merge_queue(entity_b_id);
COMMENT ON TABLE entity_merge_queue IS 'Pending entity merges for review';

-- ============================================================================
-- 14. ETL Infrastructure Tables
-- ============================================================================

CREATE TABLE source_info (
    source_id SERIAL NOT NULL PRIMARY KEY,
    source VARCHAR(30) NOT NULL DEFAULT 'calaccess',
    zip_checksum VARCHAR(64) NOT NULL, -- SHA-256 of dbwebexport.zip
    zip_size_bytes BIGINT,
    load_date DATE NOT NULL DEFAULT CURRENT_DATE,
    load_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    load_completed_at TIMESTAMPTZ,
    tables_loaded INTEGER,
    rows_loaded BIGINT,
    notes TEXT
);
CREATE INDEX idx_source_info_load_date ON source_info(load_date);
COMMENT ON TABLE source_info IS 'Data source metadata (zip checksum, load date)';

CREATE TABLE load_checkpoint (
    checkpoint_id SERIAL NOT NULL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'calaccess',
    file_hash VARCHAR(64) NOT NULL, -- SHA-256 of the file
    source_file VARCHAR(200), -- e.g., 'CalAccess/DATA/RCPT_CD.TSV'
    processed_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rows_processed INTEGER,
    notes TEXT
);
CREATE UNIQUE INDEX idx_load_checkpoint_table_hash ON load_checkpoint(table_name, source, file_hash);
CREATE INDEX idx_load_checkpoint_date ON load_checkpoint(processed_date);
COMMENT ON TABLE load_checkpoint IS 'ETL load checkpoints (idempotent re-runs)';

CREATE TABLE etl_dead_letter (
    dead_letter_id BIGSERIAL NOT NULL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    source_file VARCHAR(200),
    row_data JSONB NOT NULL,
    error_message TEXT NOT NULL,
    error_code VARCHAR(30),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(100)
);
CREATE INDEX idx_dead_letter_table ON etl_dead_letter(table_name);
CREATE INDEX idx_dead_letter_created ON etl_dead_letter(created_at);
CREATE INDEX idx_dead_letter_resolved ON etl_dead_letter(resolved_at) WHERE resolved_at IS NOT NULL;
COMMENT ON TABLE etl_dead_letter IS 'Bad row quarantine';

-- ============================================================================
-- 15. Indexes for performance on analytical queries
-- ============================================================================

-- Composite indexes for common analytical queries
CREATE INDEX idx_rcpt_cd_filer_receipt ON rcpt_cd(filer_id, receipt_dt);
CREATE INDEX idx_rcpt_cd_committee_receipt ON rcpt_cd(committee_id, receipt_dt);
CREATE INDEX idx_rcpt_cd_amount_dt ON rcpt_cd(amount DESC, receipt_dt) WHERE amount > 0;
CREATE INDEX idx_rcpt_cd_cand_election ON rcpt_cd(cand_office, election_date) WHERE cand_office IS NOT NULL;

CREATE INDEX idx_exppd_cd_filer_date ON exppd_cd(filer_id, expn_date);
CREATE INDEX idx_exppd_cd_amount_date ON exppd_cd(amount DESC, expn_date) WHERE amount > 0;
CREATE INDEX idx_exppd_cd_payee ON exppd_cd(payee_naml, expn_date) WHERE payee_naml IS NOT NULL;

CREATE INDEX idx_s401_filer_date ON s401_cd(filer_id, expn_date);
CREATE INDEX idx_s401_amount ON s401_cd(expn_amt DESC) WHERE expn_amt > 0;

CREATE INDEX idx_s497_filer_date ON s497_cd(filer_id, receipt_dt);
CREATE INDEX idx_s497_amount ON s497_cd(amount DESC) WHERE amount > 1000;

CREATE INDEX idx_s498_filer_date ON s498_cd(filer_id, expn_date);
CREATE INDEX idx_s498_amount ON s498_cd(expn_amt DESC) WHERE expn_amt > 10000;

-- Entity resolution indexes (fuzzy matching)
CREATE INDEX idx_entity_naml_gin ON entity USING gin(to_tsvector('simple', naml)) WHERE naml IS NOT NULL;
CREATE INDEX idx_entity_fullname_gin ON entity USING gin(to_tsvector('simple', fullname)) WHERE fullname IS NOT NULL;
CREATE INDEX idx_entity_alias_name_gin ON entity_alias USING gin(to_tsvector('simple', alias_name));

-- ============================================================================
-- 16. Views for common analytical queries
-- ============================================================================

-- Total contributions by candidate (all years)
CREATE OR REPLACE VIEW v_candidate_contributions AS
SELECT
    r.cand_naml || COALESCE(' ' || r.cand_namf, '') AS candidate_name,
    r.cand_office,
    r.election_date,
    r.election_type,
    COUNT(*) AS contribution_count,
    COALESCE(SUM(r.amount), 0) AS total_amount,
    MIN(r.amount) AS min_amount,
    MAX(r.amount) AS max_amount
FROM rcpt_cd r
WHERE r.cand_naml IS NOT NULL
    AND r.amount IS NOT NULL
    AND r.amount > 0
GROUP BY r.cand_naml, r.cand_namf, r.cand_office, r.election_date, r.election_type;

COMMENT ON VIEW v_candidate_contributions IS 'Total contributions by candidate and election';

-- Committee financial summary (latest filing)
CREATE OR REPLACE VIEW v_committee_summary AS
SELECT
    f.filer_id,
    fn.naml AS committee_name,
    ft.filer_type,
    r.election_date,
    cv.cash_on_hand,
    cv.total_contributions,
    cv.total_expenditures,
    cv.loans_received,
    cv.debts_owed,
    cv.total_contributions - cv.total_expenditures - cv.loans_received + cv.loan_repayments AS net_position
FROM filings f
JOIN filername fn ON f.filer_id = fn.filer_id
JOIN filer_type_assignments ft ON f.filer_id = ft.filer_id AND ft.active = TRUE
JOIN cvr_campaign_disclosure cv ON f.filing_id = cv.filing_id
WHERE fn.effect_dt = (
    SELECT MAX(fn2.effect_dt) FROM filername fn2 WHERE fn2.filer_id = fn.filer_id
);

COMMENT ON VIEW v_committee_summary IS 'Latest filing summary per committee';

-- Top contributors (by total amount given)
CREATE OR REPLACE VIEW v_top_contributors AS
SELECT
    c.ctrib_naml || COALESCE(' ' || c.ctrib_namf, '') AS contributor_name,
    c.ctrib_emp,
    c.ctrib_occ,
    c.total_gives,
    c.total_year,
    COUNT(DISTINCT r.committee_id) AS committees_contributed_to
FROM cntrb_cd c
LEFT JOIN rcpt_cd r ON c.ctrib_id = r.ctrib_id
GROUP BY c.ctrib_id, c.ctrib_naml, c.ctrib_namf, c.ctrib_emp, c.ctrib_occ, c.total_gives, c.total_year
ORDER BY c.total_gives DESC NULLS LAST
LIMIT 1000;

COMMENT ON VIEW v_top_contributors IS 'Top 1000 contributors by lifetime giving';

-- Lobbying activity summary
CREATE OR REPLACE VIEW v_lobbying_activity AS
SELECT
    ld.lby_reg_id,
    ld.lby_reg_name,
    ld.principal_id,
    ld.principal_name,
    ld.from_date,
    ld.thru_date,
    COUNT(lemp.filing_id) AS filings_count,
    COUNT(CASE WHEN lemp.activities_desc IS NOT NULL THEN 1 END) AS activity_descriptions
FROM cvr_lobby_disclosure ld
LEFT JOIN lemp_cd lemp ON ld.filer_id = lemp.filer_id
GROUP BY ld.lby_reg_id, ld.lby_reg_name, ld.principal_id, ld.principal_name,
         ld.from_date, ld.thru_date;

COMMENT ON VIEW v_lobbying_activity IS 'Lobbying activity per registered lobbyist';

-- ============================================================================
-- 17. Grant permissions (if needed for MCP reader role)
-- ============================================================================

-- Create read-only role for MCP server
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cfdb_reader') THEN
        CREATE ROLE cfdb_reader LOGIN PASSWORD 'cfdb_reader';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO cfdb_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfdb_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO cfdb_reader;

-- ============================================================================
-- End of migration
-- ============================================================================
