-- ============================================================================
-- Campaign Finance Disclosure Database — Schema v2 (aligned to real CAL-ACCESS export)
-- Phase 1: California State (CAL-ACCESS)
-- ============================================================================
--
-- Source: California Secretary of State, CAL-ACCESS Raw Data
--   https://www.sos.ca.gov/campaign-lobbying/helpful-resources/raw-data-campaign-finance-and-lobbying-activity
--
-- Generated from the real export (verified 2026-07):
--   - Column sets: actual TSV headers of the 80 files under CalAccess/DATA/
--   - Column types + primary keys: official SOS data-model document
--     (CalAccessTablesWeb.pdf, shipped inside the export)
--   - Columns absent from the 2002 doc: types inferred from sample data
--
-- Conventions:
--   - Table names = lowercased TSV filename stems (rcpt_cd, expn_cd, ...)
--   - Column names = lowercased TSV headers
--   - Money: NUMERIC(p,s) as documented; dates: TIMESTAMP (export values
--     use M/D/YYYY with optional h:mm:ss AM/PM — coerced in the ETL)
--   - NOT NULL only on primary-key columns (export blanks -> NULL)
--   - Tables without a documented PK in the export get a surrogate
--     id BIGSERIAL (append-only loads)
--   - Scraper-owned tables (filing_calendar, election_results, entity*)
--     are NOT sourced from CAL-ACCESS; kept for the scraper pipelines
--   - ETL infrastructure: load_checkpoint, etl_dead_letter
-- ============================================================================

-- Fuzzy-matching extensions (optional; created only when available)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm')
    AND NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE EXTENSION pg_trgm;
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'fuzzystrmatch')
    AND NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'fuzzystrmatch') THEN
        CREATE EXTENSION fuzzystrmatch;
    END IF;
END $$;

-- CalAccess/DATA/ACRONYMS_CD.TSV  (doc table: ACRONYMS)
CREATE TABLE IF NOT EXISTS acronyms_cd (
    acronym TEXT,
    stands_for TEXT,
    effect_dt TIMESTAMP,
    a_desc TEXT,
    PRIMARY KEY (acronym)
);

-- CalAccess/DATA/ADDRESS_CD.TSV  (doc table: ADDRESS)
CREATE TABLE IF NOT EXISTS address_cd (
    adrid INTEGER,
    city TEXT,
    st TEXT,
    zip4 TEXT,
    phon TEXT,
    fax TEXT,
    email TEXT,
    PRIMARY KEY (adrid)
);

-- CalAccess/DATA/BALLOT_MEASURES_CD.TSV  (doc table: BALLOT_MEASURES)
CREATE TABLE IF NOT EXISTS ballot_measures_cd (
    election_date TIMESTAMP,
    filer_id INTEGER,
    measure_no TEXT,
    measure_name TEXT,
    measure_short_name TEXT,
    jurisdiction TEXT,
    PRIMARY KEY (filer_id)
);

-- CalAccess/DATA/CVR2_CAMPAIGN_DISCLOSURE_CD.TSV  (doc table: CVR2_CAMPAIGN_DISCLOSURE)
CREATE TABLE IF NOT EXISTS cvr2_campaign_disclosure_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    title TEXT,
    mail_city TEXT,
    mail_st TEXT,
    mail_zip4 TEXT,
    f460_part TEXT,
    cmte_id TEXT,
    enty_naml TEXT,
    enty_namf TEXT,
    enty_namt TEXT,
    enty_nams TEXT,
    enty_city TEXT,
    enty_st TEXT,
    enty_zip4 TEXT,
    enty_phon TEXT,
    enty_fax TEXT,
    enty_email TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    control_yn TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    sup_opp_cd TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/CVR2_LOBBY_DISCLOSURE_CD.TSV  (doc table: CVR2_LOBBY_DISCLOSURE)
CREATE TABLE IF NOT EXISTS cvr2_lobby_disclosure_cd (
    amend_id INTEGER,
    entity_cd TEXT,
    entity_id TEXT,
    enty_namf TEXT,
    enty_naml TEXT,
    enty_nams TEXT,
    enty_namt TEXT,
    enty_title TEXT,
    filing_id INTEGER,
    form_type TEXT,
    line_item INTEGER,
    rec_type TEXT,
    tran_id TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/CVR2_REGISTRATION_CD.TSV  (doc table: CVR2_REGISTRATION)
CREATE TABLE IF NOT EXISTS cvr2_registration_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    entity_id TEXT,
    enty_naml TEXT,
    enty_namf TEXT,
    enty_namt TEXT,
    enty_nams TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/CVR2_SO_CD.TSV  (doc table: CVR2_SO)
CREATE TABLE IF NOT EXISTS cvr2_so_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    enty_naml TEXT,
    enty_namf TEXT,
    enty_namt TEXT,
    enty_nams TEXT,
    item_cd TEXT,
    mail_city TEXT,
    mail_st TEXT,
    mail_zip4 TEXT,
    day_phone TEXT,
    fax_phone TEXT,
    email_adr TEXT,
    cmte_id TEXT,
    ind_group TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    non_pty_cb TEXT,
    party_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    sup_opp_cd TEXT,
    year_elect TEXT,
    pof_title TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/CVR3_VERIFICATION_INFO_CD.TSV  (doc table: CVR3_VERIFICATION_INFO)
CREATE TABLE IF NOT EXISTS cvr3_verification_info_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    sig_date TIMESTAMP,
    sig_loc TEXT,
    sig_naml TEXT,
    sig_namf TEXT,
    sig_namt TEXT,
    sig_nams TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/CVR_CAMPAIGN_DISCLOSURE_CD.TSV  (doc table: CVR_CAMPAIGN_DISCLOSURE)
CREATE TABLE IF NOT EXISTS cvr_campaign_disclosure_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    filer_id TEXT,
    entity_cd TEXT,
    filer_naml TEXT,
    filer_namf TEXT,
    filer_namt TEXT,
    filer_nams TEXT,
    report_num TEXT,
    rpt_date TIMESTAMP,
    stmt_type TEXT,
    late_rptno TEXT,
    from_date TIMESTAMP,
    thru_date TIMESTAMP,
    elect_date TIMESTAMP,
    filer_city TEXT,
    filer_st TEXT,
    filer_zip4 TEXT,
    filer_phon TEXT,
    filer_fax TEXT,
    file_email TEXT,
    mail_city TEXT,
    mail_st TEXT,
    mail_zip4 TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    tres_city TEXT,
    tres_st TEXT,
    tres_zip4 TEXT,
    tres_phon TEXT,
    tres_fax TEXT,
    tres_email TEXT,
    cmtte_type TEXT,
    control_yn TEXT,
    sponsor_yn TEXT,
    primfrm_yn TEXT,
    brdbase_yn TEXT,
    amendexp_1 TEXT,
    amendexp_2 TEXT,
    amendexp_3 TEXT,
    rpt_att_cb TEXT,
    cmtte_id TEXT,
    reportname TEXT,
    rptfromdt TIMESTAMP,
    rptthrudt TIMESTAMP,
    emplbus_cb TEXT,
    bus_name TEXT,
    bus_city TEXT,
    bus_st TEXT,
    bus_zip4 TEXT,
    bus_inter TEXT,
    busact_cb TEXT,
    busactvity TEXT,
    assoc_cb TEXT,
    assoc_int TEXT,
    other_cb TEXT,
    other_int TEXT,
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    cand_city TEXT,
    cand_st TEXT,
    cand_zip4 TEXT,
    cand_phon TEXT,
    cand_fax TEXT,
    cand_email TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    sup_opp_cd TEXT,
    employer TEXT,
    occupation TEXT,
    selfemp_cb TEXT,
    bal_id TEXT,
    cand_id TEXT,
    PRIMARY KEY (amend_id, filing_id)
);

-- CalAccess/DATA/CVR_E530_CD.TSV  (doc table: CVR_E530)
CREATE TABLE IF NOT EXISTS cvr_e530_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    entity_cd TEXT,
    filer_naml TEXT,
    filer_namf TEXT,
    filer_namt TEXT,
    filer_nams TEXT,
    report_num TEXT,
    rpt_date TIMESTAMP,
    filer_city TEXT,
    filer_st TEXT,
    filer_zip4 TEXT,
    occupation TEXT,
    employer TEXT,
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    district_cd INTEGER,
    office_cd INTEGER,
    pmnt_dt TIMESTAMP,
    pmnt_amount NUMERIC(12,2),
    type_literature TEXT,
    type_printads TEXT,
    type_radio TEXT,
    type_tv TEXT,
    type_it TEXT,
    type_billboards TEXT,
    type_other TEXT,
    other_desc TEXT,
    PRIMARY KEY (amend_id, filing_id)
);

-- CalAccess/DATA/CVR_F470_CD.TSV  (doc table: CVR_F470)
CREATE TABLE IF NOT EXISTS cvr_f470_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    filer_id TEXT,
    entity_cd TEXT,
    filer_naml TEXT,
    filer_namf TEXT,
    filer_namt TEXT,
    filer_nams TEXT,
    report_num TEXT,
    rpt_date TIMESTAMP,
    cand_city TEXT,
    cand_st TEXT,
    cand_zip4 TEXT,
    cand_phon TEXT,
    cand_fax TEXT,
    cand_email TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    elect_date TIMESTAMP,
    date_1000 TIMESTAMP,
    PRIMARY KEY (amend_id, filing_id, form_type, rec_type)
);

-- CalAccess/DATA/CVR_LOBBY_DISCLOSURE_CD.TSV  (doc table: CVR_LOBBY_DISCLOSURE)
CREATE TABLE IF NOT EXISTS cvr_lobby_disclosure_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    sender_id TEXT,
    filer_id TEXT,
    entity_cd TEXT,
    filer_naml TEXT,
    filer_namf TEXT,
    filer_namt TEXT,
    filer_nams TEXT,
    report_num TEXT,
    rpt_date TIMESTAMP,
    from_date TIMESTAMP,
    thru_date TIMESTAMP,
    cum_beg_dt TIMESTAMP,
    firm_id TEXT,
    firm_name TEXT,
    firm_city TEXT,
    firm_st TEXT,
    firm_zip4 TEXT,
    firm_phon TEXT,
    mail_city TEXT,
    mail_st TEXT,
    mail_zip4 TEXT,
    mail_phon TEXT,
    sig_date TIMESTAMP,
    sig_loc TEXT,
    sig_naml TEXT,
    sig_namf TEXT,
    sig_namt TEXT,
    sig_nams TEXT,
    prn_naml TEXT,
    prn_namf TEXT,
    prn_namt TEXT,
    prn_nams TEXT,
    sig_title TEXT,
    nopart1_cb TEXT,
    nopart2_cb TEXT,
    part1_1_cb TEXT,
    part1_2_cb TEXT,
    ctrib_n_cb TEXT,
    ctrib_y_cb TEXT,
    lby_actvty TEXT,
    lobby_n_cb TEXT,
    lobby_y_cb TEXT,
    major_naml TEXT,
    major_namf TEXT,
    major_namt TEXT,
    major_nams TEXT,
    rcpcmte_nm TEXT,
    rcpcmte_id TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, rec_type)
);

-- CalAccess/DATA/CVR_REGISTRATION_CD.TSV  (doc table: CVR_REGISTRATION)
CREATE TABLE IF NOT EXISTS cvr_registration_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    sender_id TEXT,
    filer_id TEXT,
    entity_cd TEXT,
    filer_naml TEXT,
    filer_namf TEXT,
    filer_namt TEXT,
    filer_nams TEXT,
    report_num TEXT,
    rpt_date TIMESTAMP,
    ls_beg_yr TEXT,
    ls_end_yr TEXT,
    qual_date TIMESTAMP,
    eff_date TIMESTAMP,
    bus_city TEXT,
    bus_st TEXT,
    bus_zip4 TEXT,
    bus_phon TEXT,
    bus_fax TEXT,
    bus_email TEXT,
    mail_city TEXT,
    mail_st TEXT,
    mail_zip4 TEXT,
    mail_phon TEXT,
    sig_date TIMESTAMP,
    sig_loc TEXT,
    sig_naml TEXT,
    sig_namf TEXT,
    sig_namt TEXT,
    sig_nams TEXT,
    prn_naml TEXT,
    prn_namf TEXT,
    prn_namt TEXT,
    prn_nams TEXT,
    sig_title TEXT,
    stmt_firm TEXT,
    ind_cb TEXT,
    bus_cb TEXT,
    trade_cb TEXT,
    oth_cb TEXT,
    a_b_name TEXT,
    a_b_city TEXT,
    a_b_st TEXT,
    a_b_zip4 TEXT,
    descrip_1 TEXT,
    descrip_2 TEXT,
    c_less50 TEXT,
    c_more50 TEXT,
    ind_class TEXT,
    ind_descr TEXT,
    bus_class TEXT,
    bus_descr TEXT,
    auth_name TEXT,
    auth_city TEXT,
    auth_st TEXT,
    auth_zip4 TEXT,
    lobby_int TEXT,
    influen_yn TEXT,
    firm_name TEXT,
    newcert_cb TEXT,
    rencert_cb TEXT,
    complet_dt TIMESTAMP,
    lby_reg_cb TEXT,
    lby_604_cb TEXT,
    st_leg_yn TEXT,
    st_agency TEXT,
    lobby_cb TEXT,
    l_firm_cb TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, rec_type)
);

-- CalAccess/DATA/CVR_SO_CD.TSV  (doc table: CVR_SO)
CREATE TABLE IF NOT EXISTS cvr_so_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    filer_id TEXT,
    entity_cd TEXT,
    filer_naml TEXT,
    filer_namf TEXT,
    filer_namt TEXT,
    filer_nams TEXT,
    report_num TEXT,
    rpt_date TIMESTAMP,
    qual_cb TEXT,
    qualfy_dt TIMESTAMP,
    term_date TIMESTAMP,
    city TEXT,
    st TEXT,
    zip4 TEXT,
    phone TEXT,
    county_res TEXT,
    county_act TEXT,
    mail_city TEXT,
    mail_st TEXT,
    mail_zip4 TEXT,
    cmte_fax TEXT,
    cmte_email TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    tres_city TEXT,
    tres_st TEXT,
    tres_zip4 TEXT,
    tres_phon TEXT,
    actvty_lvl TEXT,
    com82013yn TEXT,
    com82013nm TEXT,
    com82013id TEXT,
    control_cb TEXT,
    bank_nam TEXT,
    bank_adr1 TEXT,
    bank_adr2 TEXT,
    bank_city TEXT,
    bank_st TEXT,
    bank_zip4 TEXT,
    bank_phon TEXT,
    acct_opendt TIMESTAMP,
    surplusdsp TEXT,
    primfc_cb TEXT,
    genpurp_cb TEXT,
    gpc_descr TEXT,
    sponsor_cb TEXT,
    brdbase_cb TEXT,
    smcont_qualdt TIMESTAMP,
    PRIMARY KEY (amend_id, filing_id, form_type, rec_type)
);

-- CalAccess/DATA/DEBT_CD.TSV  (doc table: DEBT)
CREATE TABLE IF NOT EXISTS debt_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    payee_naml TEXT,
    payee_namf TEXT,
    payee_namt TEXT,
    payee_nams TEXT,
    payee_city TEXT,
    payee_st TEXT,
    payee_zip4 TEXT,
    beg_bal NUMERIC(12,2),
    amt_incur NUMERIC(12,2),
    amt_paid NUMERIC(12,2),
    end_bal NUMERIC(12,2),
    expn_code TEXT,
    expn_dscr TEXT,
    cmte_id TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    tres_city TEXT,
    tres_st TEXT,
    tres_zip4 TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    xref_schnm TEXT,
    xref_match TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/EFS_FILING_LOG_CD.TSV  (doc table: EFS_FILING_LOG)
CREATE TABLE IF NOT EXISTS efs_filing_log_cd (
    id BIGSERIAL,
    filing_date TIMESTAMP,
    filingstatus TEXT,
    vendor TEXT,
    filer_id TEXT,
    form_type TEXT,
    error_no TEXT,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/EXPN_CD.TSV  (doc table: EXPN)
CREATE TABLE IF NOT EXISTS expn_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    payee_naml TEXT,
    payee_namf TEXT,
    payee_namt TEXT,
    payee_nams TEXT,
    payee_city TEXT,
    payee_st TEXT,
    payee_zip4 TEXT,
    expn_date TIMESTAMP,
    amount NUMERIC(12,2),
    cum_ytd NUMERIC(12,2),
    cum_oth NUMERIC(12,2),
    expn_chkno TEXT,
    expn_code TEXT,
    expn_dscr TEXT,
    agent_naml TEXT,
    agent_namf TEXT,
    agent_namt TEXT,
    agent_nams TEXT,
    cmte_id TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    tres_city TEXT,
    tres_st TEXT,
    tres_zip4 TEXT,
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    sup_opp_cd TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    g_from_e_f TEXT,
    xref_schnm TEXT,
    xref_match TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);
CREATE INDEX IF NOT EXISTS idx_expn_cd_cmte_id ON expn_cd (cmte_id);
CREATE INDEX IF NOT EXISTS idx_expn_cd_payee_naml ON expn_cd (payee_naml);

-- CalAccess/DATA/F495P2_CD.TSV  (doc table: F495P2)
CREATE TABLE IF NOT EXISTS f495p2_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    elect_date TIMESTAMP,
    electjuris TEXT,
    contribamt NUMERIC(12,2),
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/F501_502_CD.TSV  (doc table: F501_502)
CREATE TABLE IF NOT EXISTS f501_502_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    filer_id TEXT,
    committee_id TEXT,
    entity_cd INTEGER,
    report_num TEXT,
    rpt_date TIMESTAMP,
    stmt_type INTEGER,
    from_date TIMESTAMP,
    thru_date TIMESTAMP,
    elect_date TIMESTAMP,
    cand_naml TEXT,
    cand_namf TEXT,
    can_namm TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    moniker_pos INTEGER,
    moniker TEXT,
    cand_city TEXT,
    cand_st TEXT,
    cand_zip4 TEXT,
    cand_phon TEXT,
    cand_fax TEXT,
    cand_email TEXT,
    fin_naml TEXT,
    fin_namf TEXT,
    fin_namt TEXT,
    fin_nams TEXT,
    fin_city TEXT,
    fin_st TEXT,
    fin_zip4 TEXT,
    fin_phon TEXT,
    fin_fax TEXT,
    fin_email TEXT,
    office_cd INTEGER,
    offic_dscr TEXT,
    agency_nam TEXT,
    juris_cd INTEGER,
    juris_dscr TEXT,
    dist_no TEXT,
    party TEXT,
    yr_of_elec INTEGER,
    elec_type INTEGER,
    execute_dt TIMESTAMP,
    can_sig TEXT,
    account_no TEXT,
    acct_op_dt TIMESTAMP,
    party_cd INTEGER,
    district_cd INTEGER,
    accept_limit_yn TEXT,
    did_exceed_dt TIMESTAMP,
    cntrb_prsnl_fnds_dt TEXT,
    PRIMARY KEY (amend_id, filing_id)
);

-- CalAccess/DATA/F690P2_CD.TSV  (doc table: F690P2)
CREATE TABLE IF NOT EXISTS f690p2_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    exec_date TIMESTAMP,
    from_date TIMESTAMP,
    thru_date TIMESTAMP,
    chg_parts TEXT,
    chg_sects TEXT,
    amend_txt1 TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/FILERNAME_CD.TSV  (doc table: FILER_NAMES)
CREATE TABLE IF NOT EXISTS filername_cd (
    id BIGSERIAL,
    xref_filer_id TEXT,
    filer_id INTEGER,
    filer_type TEXT,
    status TEXT,
    effect_dt TIMESTAMP,
    naml TEXT,
    namf TEXT,
    namt TEXT,
    nams TEXT,
    adr1 TEXT,
    adr2 TEXT,
    city TEXT,
    st TEXT,
    zip4 TEXT,
    phon TEXT,
    fax TEXT,
    email TEXT,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/FILERS_CD.TSV  (doc table: FILERS)
CREATE TABLE IF NOT EXISTS filers_cd (
    filer_id INTEGER,
    PRIMARY KEY (filer_id)
);

-- CalAccess/DATA/FILER_ACRONYMS_CD.TSV  (doc table: FILER_ACRONYMS)
CREATE TABLE IF NOT EXISTS filer_acronyms_cd (
    acronym TEXT,
    filer_id INTEGER,
    PRIMARY KEY (acronym, filer_id)
);

-- CalAccess/DATA/FILER_ADDRESS_CD.TSV  (doc table: FILER_ADDRESS)
CREATE TABLE IF NOT EXISTS filer_address_cd (
    filer_id INTEGER,
    adrid INTEGER,
    effect_dt TIMESTAMP,
    add_type INTEGER,
    session_id INTEGER,
    PRIMARY KEY (adrid, filer_id)
);

-- CalAccess/DATA/FILER_ETHICS_CLASS_CD.TSV  (doc table: FILER_ETHICS_CLASS)
CREATE TABLE IF NOT EXISTS filer_ethics_class_cd (
    filer_id INTEGER,
    session_id INTEGER,
    ethics_date TIMESTAMP,
    PRIMARY KEY (ethics_date, filer_id, session_id)
);

-- CalAccess/DATA/FILER_FILINGS_CD.TSV  (doc table: FILER_FILINGS)
CREATE TABLE IF NOT EXISTS filer_filings_cd (
    filer_id INTEGER,
    filing_id INTEGER,
    period_id INTEGER,
    form_id TEXT,
    filing_sequence INTEGER,
    filing_date TIMESTAMP,
    stmnt_type INTEGER,
    stmnt_status INTEGER,
    session_id INTEGER,
    user_id TEXT,
    special_audit INTEGER,
    fine_audit INTEGER,
    rpt_start TIMESTAMP,
    rpt_end TIMESTAMP,
    rpt_date TIMESTAMP,
    filing_type TEXT,
    PRIMARY KEY (filer_id, filing_id, filing_sequence, form_id)
);

-- CalAccess/DATA/FILER_INTERESTS_CD.TSV  (doc table: FILER_INTERESTS)
CREATE TABLE IF NOT EXISTS filer_interests_cd (
    filer_id INTEGER,
    session_id INTEGER,
    interest_cd INTEGER,
    effect_date TIMESTAMP,
    PRIMARY KEY (effect_date, filer_id, interest_cd, session_id)
);

-- CalAccess/DATA/FILER_LINKS_CD.TSV  (doc table: FILER_LINKS)
CREATE TABLE IF NOT EXISTS filer_links_cd (
    filer_id_a INTEGER,
    filer_id_b INTEGER,
    active_flg TEXT,
    session_id INTEGER,
    link_type INTEGER,
    link_desc TEXT,
    effect_dt TIMESTAMP,
    dominate_filer TEXT,
    termination_dt TIMESTAMP,
    PRIMARY KEY (active_flg, filer_id_a, filer_id_b, link_type, session_id)
);

-- CalAccess/DATA/FILER_STATUS_TYPES_CD.TSV  (doc table: FILER_STATUS_TYPES)
CREATE TABLE IF NOT EXISTS filer_status_types_cd (
    status_type TEXT,
    status_desc TEXT,
    PRIMARY KEY (status_type)
);

-- CalAccess/DATA/FILER_TO_FILER_TYPE_CD.TSV  (doc table: FILER_TO_FILER_TYPE)
CREATE TABLE IF NOT EXISTS filer_to_filer_type_cd (
    filer_id INTEGER,
    filer_type INTEGER,
    active TEXT,
    race INTEGER,
    session_id INTEGER,
    category INTEGER,
    category_type INTEGER,
    sub_category INTEGER,
    effect_dt TIMESTAMP,
    sub_category_type INTEGER,
    election_type INTEGER,
    sub_category_a TEXT,
    nyq_dt TIMESTAMP,
    party_cd INTEGER,
    county_cd INTEGER,
    district_cd INTEGER,
    PRIMARY KEY (effect_dt, filer_id, filer_type, session_id)
);

-- CalAccess/DATA/FILER_TYPES_CD.TSV  (doc table: FILER_TYPES)
CREATE TABLE IF NOT EXISTS filer_types_cd (
    filer_type INTEGER,
    description TEXT,
    grp_type INTEGER,
    calc_use TEXT,
    grace_period INTEGER,
    PRIMARY KEY (filer_type)
);

-- CalAccess/DATA/FILER_TYPE_PERIODS_CD.TSV  (doc table: FILER_TYPE_PERIODS)
CREATE TABLE IF NOT EXISTS filer_type_periods_cd (
    election_type INTEGER,
    filer_type INTEGER,
    period_id INTEGER,
    PRIMARY KEY (election_type, filer_type, period_id)
);

-- CalAccess/DATA/FILER_XREF_CD.TSV  (doc table: FILER_XREF)
CREATE TABLE IF NOT EXISTS filer_xref_cd (
    filer_id INTEGER,
    xref_id TEXT,
    effect_dt TIMESTAMP,
    migration_source TEXT,
    PRIMARY KEY (filer_id, xref_id)
);

-- CalAccess/DATA/FILINGS_CD.TSV  (doc table: FILINGS)
CREATE TABLE IF NOT EXISTS filings_cd (
    filing_id INTEGER,
    filing_type INTEGER,
    PRIMARY KEY (filing_id)
);

-- CalAccess/DATA/FILING_PERIOD_CD.TSV  (doc table: FILING_PERIOD)
CREATE TABLE IF NOT EXISTS filing_period_cd (
    period_id INTEGER,
    start_date TIMESTAMP,
    end_date TIMESTAMP,
    period_type INTEGER,
    per_grp_type INTEGER,
    period_desc TEXT,
    deadline TIMESTAMP,
    PRIMARY KEY (period_id)
);

-- CalAccess/DATA/GROUP_TYPES_CD.TSV  (doc table: GROUP_TYPES)
CREATE TABLE IF NOT EXISTS group_types_cd (
    grp_id INTEGER,
    grp_name TEXT,
    grp_desc TEXT,
    PRIMARY KEY (grp_id)
);

-- CalAccess/DATA/HDR_CD.TSV  (doc table: HDR)
CREATE TABLE IF NOT EXISTS hdr_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    ef_type TEXT,
    state_cd TEXT,
    cal_ver TEXT,
    soft_name TEXT,
    soft_ver TEXT,
    hdrcomment TEXT,
    PRIMARY KEY (amend_id, filing_id)
);

-- CalAccess/DATA/HEADER_CD.TSV  (doc table: HEADER)
CREATE TABLE IF NOT EXISTS header_cd (
    line_number INTEGER,
    form_id TEXT,
    rec_type TEXT,
    section_label TEXT,
    comments1 TEXT,
    comments2 TEXT,
    label TEXT,
    column_a NUMERIC(12,2),
    column_b NUMERIC(12,2),
    column_c NUMERIC(12,2),
    show_c INTEGER,
    show_b INTEGER,
    PRIMARY KEY (form_id, line_number, rec_type)
);

-- CalAccess/DATA/IMAGE_LINKS_CD.TSV  (doc table: IMAGE_LINKS)
CREATE TABLE IF NOT EXISTS image_links_cd (
    img_link_id INTEGER,
    img_link_type INTEGER,
    img_id INTEGER,
    img_type INTEGER,
    img_dt TIMESTAMP,
    PRIMARY KEY (img_id, img_link_id)
);

-- CalAccess/DATA/LATT_CD.TSV  (doc table: LATT)
CREATE TABLE IF NOT EXISTS latt_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    recip_naml TEXT,
    recip_namf TEXT,
    recip_namt TEXT,
    recip_nams TEXT,
    recip_city TEXT,
    recip_st TEXT,
    recip_zip4 TEXT,
    pmt_date TIMESTAMP,
    amount NUMERIC(12,2),
    cum_amt NUMERIC(12,2),
    cumbeg_dt TIMESTAMP,
    memo_code TEXT,
    memo_refno TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/LCCM_CD.TSV  (doc table: LCCM)
CREATE TABLE IF NOT EXISTS lccm_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    recip_naml TEXT,
    recip_namf TEXT,
    recip_namt TEXT,
    recip_nams TEXT,
    recip_city TEXT,
    recip_st TEXT,
    recip_zip4 TEXT,
    recip_id TEXT,
    ctrib_naml TEXT,
    ctrib_namf TEXT,
    ctrib_namt TEXT,
    ctrib_nams TEXT,
    ctrib_date TIMESTAMP,
    amount NUMERIC(12,2),
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/LEGISLATIVE_SESSIONS_CD.TSV  (doc table: LEGISLATIVE_SESSIONS)
CREATE TABLE IF NOT EXISTS legislative_sessions_cd (
    session_id INTEGER,
    begin_date TIMESTAMP,
    end_date TIMESTAMP,
    PRIMARY KEY (session_id)
);

-- CalAccess/DATA/LEMP_CD.TSV  (doc table: LEMP)
CREATE TABLE IF NOT EXISTS lemp_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    client_id TEXT,
    cli_naml TEXT,
    cli_namf TEXT,
    cli_namt TEXT,
    cli_nams TEXT,
    cli_city TEXT,
    cli_st TEXT,
    cli_zip4 TEXT,
    cli_phon TEXT,
    eff_date TIMESTAMP,
    con_period TEXT,
    agencylist TEXT,
    descrip TEXT,
    subfirm_id TEXT,
    sub_name TEXT,
    sub_city TEXT,
    sub_st TEXT,
    sub_zip4 TEXT,
    sub_phon TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/LEXP_CD.TSV  (doc table: LEXP)
CREATE TABLE IF NOT EXISTS lexp_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    recsubtype TEXT,
    entity_cd TEXT,
    payee_naml TEXT,
    payee_namf TEXT,
    payee_namt TEXT,
    payee_nams TEXT,
    payee_city TEXT,
    payee_st TEXT,
    payee_zip4 TEXT,
    credcardco TEXT,
    bene_name TEXT,
    bene_posit TEXT,
    bene_amt TEXT,
    expn_dscr TEXT,
    expn_date TIMESTAMP,
    amount NUMERIC(12,2),
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/LOAN_CD.TSV  (doc table: LOAN)
CREATE TABLE IF NOT EXISTS loan_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    loan_type TEXT,
    entity_cd TEXT,
    lndr_naml TEXT,
    lndr_namf TEXT,
    lndr_namt TEXT,
    lndr_nams TEXT,
    loan_city TEXT,
    loan_st TEXT,
    loan_zip4 TEXT,
    loan_date1 TIMESTAMP,
    loan_date2 TIMESTAMP,
    loan_amt1 NUMERIC(12,2),
    loan_amt2 NUMERIC(12,2),
    loan_amt3 NUMERIC(12,2),
    loan_amt4 NUMERIC(12,2),
    loan_rate TEXT,
    loan_emp TEXT,
    loan_occ TEXT,
    loan_self TEXT,
    cmte_id TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    tres_city TEXT,
    tres_st TEXT,
    tres_zip4 TEXT,
    intr_naml TEXT,
    intr_namf TEXT,
    intr_namt TEXT,
    intr_nams TEXT,
    intr_city TEXT,
    intr_st TEXT,
    intr_zip4 TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    xref_schnm TEXT,
    xref_match TEXT,
    loan_amt5 NUMERIC(12,2),
    loan_amt6 NUMERIC(12,2),
    loan_amt7 NUMERIC(12,2),
    loan_amt8 NUMERIC(12,2),
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/LOBBYING_CHG_LOG_CD.TSV  (doc table: LOBBYING_CHG_LOG)
CREATE TABLE IF NOT EXISTS lobbying_chg_log_cd (
    filer_id INTEGER,
    change_no INTEGER,
    session_id INTEGER,
    log_dt TIMESTAMP,
    filer_type INTEGER,
    correction_flg TEXT,
    action TEXT,
    attribute_changed TEXT,
    ethics_dt TIMESTAMP,
    interests TEXT,
    filer_full_name TEXT,
    filer_city TEXT,
    filer_st TEXT,
    filer_zip TEXT,
    filer_phone TEXT,
    entity_type INTEGER,
    entity_name TEXT,
    entity_city TEXT,
    entity_st TEXT,
    entity_zip TEXT,
    entity_phone TEXT,
    entity_id INTEGER,
    responsible_officer TEXT,
    effect_dt TIMESTAMP,
    PRIMARY KEY (change_no, filer_id)
);

-- CalAccess/DATA/LOBBYIST_CONTRIBUTIONS1_CD.TSV  (doc table: LOBBYIST_CONTRIBUTIONS1)
CREATE TABLE IF NOT EXISTS lobbyist_contributions1_cd (
    id BIGSERIAL,
    filer_id INTEGER,
    filing_period_start_dt TIMESTAMP,
    filing_period_end_dt TIMESTAMP,
    contribution_dt TIMESTAMP,
    recipient_name TEXT,
    recipient_id INTEGER,
    amount NUMERIC(12,2),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_lobbyist_contributions1_cd_recipient_id ON lobbyist_contributions1_cd (recipient_id);
CREATE INDEX IF NOT EXISTS idx_lobbyist_contributions1_cd_filer_id ON lobbyist_contributions1_cd (filer_id);

-- CalAccess/DATA/LOBBYIST_CONTRIBUTIONS2_CD.TSV  (doc table: LOBBYIST_CONTRIBUTIONS2)
CREATE TABLE IF NOT EXISTS lobbyist_contributions2_cd (
    id BIGSERIAL,
    filer_id INTEGER,
    filing_period_start_dt TIMESTAMP,
    filing_period_end_dt TIMESTAMP,
    contribution_dt TIMESTAMP,
    recipient_name TEXT,
    recipient_id INTEGER,
    amount NUMERIC(12,2),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_lobbyist_contributions2_cd_recipient_id ON lobbyist_contributions2_cd (recipient_id);
CREATE INDEX IF NOT EXISTS idx_lobbyist_contributions2_cd_filer_id ON lobbyist_contributions2_cd (filer_id);

-- CalAccess/DATA/LOBBYIST_CONTRIBUTIONS3_CD.TSV  (doc table: LOBBYIST_CONTRIBUTIONS3)
CREATE TABLE IF NOT EXISTS lobbyist_contributions3_cd (
    id BIGSERIAL,
    filer_id INTEGER,
    filing_period_start_dt TIMESTAMP,
    filing_period_end_dt TIMESTAMP,
    contribution_dt TIMESTAMP,
    recipient_name TEXT,
    recipient_id INTEGER,
    amount NUMERIC(12,2),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_lobbyist_contributions3_cd_recipient_id ON lobbyist_contributions3_cd (recipient_id);
CREATE INDEX IF NOT EXISTS idx_lobbyist_contributions3_cd_filer_id ON lobbyist_contributions3_cd (filer_id);

-- CalAccess/DATA/LOBBYIST_EMPLOYER1_CD.TSV  (doc table: LOBBYIST_EMPLOYER1)
CREATE TABLE IF NOT EXISTS lobbyist_employer1_cd (
    id BIGSERIAL,
    employer_id INTEGER,
    session_id INTEGER,
    employer_name TEXT,
    current_qtr_amt NUMERIC(12,2),
    session_total_amt NUMERIC(12,2),
    contributor_id INTEGER,
    interest_cd INTEGER,
    interest_name TEXT,
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMPLOYER2_CD.TSV  (doc table: LOBBYIST_EMPLOYER2)
CREATE TABLE IF NOT EXISTS lobbyist_employer2_cd (
    id BIGSERIAL,
    employer_id INTEGER,
    session_id INTEGER,
    employer_name TEXT,
    current_qtr_amt NUMERIC(12,2),
    session_total_amt NUMERIC(12,2),
    contributor_id INTEGER,
    interest_cd INTEGER,
    interest_name TEXT,
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMPLOYER3_CD.TSV  (doc table: LOBBYIST_EMPLOYER3)
CREATE TABLE IF NOT EXISTS lobbyist_employer3_cd (
    id BIGSERIAL,
    employer_id INTEGER,
    session_id INTEGER,
    employer_name TEXT,
    current_qtr_amt NUMERIC(12,2),
    session_total_amt NUMERIC(12,2),
    contributor_id INTEGER,
    interest_cd INTEGER,
    interest_name TEXT,
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMPLOYER_FIRMS1_CD.TSV  (doc table: LOBBYIST_EMPLOYER_FIRMS1)
CREATE TABLE IF NOT EXISTS lobbyist_employer_firms1_cd (
    id BIGSERIAL,
    employer_id INTEGER,
    firm_id INTEGER,
    firm_name TEXT,
    session_id INTEGER,
    termination_dt TIMESTAMP,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMPLOYER_FIRMS2_CD.TSV  (doc table: LOBBYIST_EMPLOYER_FIRMS2)
CREATE TABLE IF NOT EXISTS lobbyist_employer_firms2_cd (
    id BIGSERIAL,
    employer_id INTEGER,
    firm_id INTEGER,
    firm_name TEXT,
    session_id INTEGER,
    termination_dt TIMESTAMP,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMPLOYER_HISTORY_CD.TSV  (doc table: LOBBYIST_EMPLOYER_HISTORY)
CREATE TABLE IF NOT EXISTS lobbyist_employer_history_cd (
    id BIGSERIAL,
    contributor_id INTEGER,
    current_qtr_amt NUMERIC(12,2),
    employer_id INTEGER,
    employer_name TEXT,
    interest_cd INTEGER,
    interest_name TEXT,
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    session_id INTEGER,
    session_total_amt NUMERIC(12,2),
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMP_LOBBYIST1_CD.TSV  (doc table: LOBBYIST_EMP_LOBBYIST1)
CREATE TABLE IF NOT EXISTS lobbyist_emp_lobbyist1_cd (
    id BIGSERIAL,
    lobbyist_id INTEGER,
    employer_id INTEGER,
    lobbyist_last_name TEXT,
    lobbyist_first_name TEXT,
    employer_name TEXT,
    session_id INTEGER,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_EMP_LOBBYIST2_CD.TSV  (doc table: LOBBYIST_EMP_LOBBYIST2)
CREATE TABLE IF NOT EXISTS lobbyist_emp_lobbyist2_cd (
    id BIGSERIAL,
    lobbyist_id INTEGER,
    employer_id INTEGER,
    lobbyist_last_name TEXT,
    lobbyist_first_name TEXT,
    employer_name TEXT,
    session_id INTEGER,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM1_CD.TSV  (doc table: LOBBYIST_FIRM1)
CREATE TABLE IF NOT EXISTS lobbyist_firm1_cd (
    id BIGSERIAL,
    firm_id INTEGER,
    session_id INTEGER,
    firm_name TEXT,
    current_qtr_amt NUMERIC(12,2),
    session_total_amt NUMERIC(12,2),
    contributor_id INTEGER,
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM2_CD.TSV  (doc table: LOBBYIST_FIRM2)
CREATE TABLE IF NOT EXISTS lobbyist_firm2_cd (
    id BIGSERIAL,
    firm_id INTEGER,
    session_id INTEGER,
    firm_name TEXT,
    current_qtr_amt NUMERIC(12,2),
    session_total_amt NUMERIC(12,2),
    contributor_id INTEGER,
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM3_CD.TSV  (doc table: LOBBYIST_FIRM3)
CREATE TABLE IF NOT EXISTS lobbyist_firm3_cd (
    id BIGSERIAL,
    firm_id INTEGER,
    session_id INTEGER,
    firm_name TEXT,
    current_qtr_amt NUMERIC(12,2),
    session_total_amt NUMERIC(12,2),
    contributor_id INTEGER,
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM_EMPLOYER1_CD.TSV  (doc table: LOBBYIST_FIRM_EMPLOYER1)
CREATE TABLE IF NOT EXISTS lobbyist_firm_employer1_cd (
    id BIGSERIAL,
    firm_id INTEGER,
    filing_id INTEGER,
    filing_sequence INTEGER,
    firm_name TEXT,
    employer_name TEXT,
    rpt_start TIMESTAMP,
    rpt_end TIMESTAMP,
    per_total NUMERIC(12,2),
    cum_total NUMERIC(12,2),
    lby_actvty TEXT,
    ext_lby_actvty TEXT,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM_EMPLOYER2_CD.TSV  (doc table: LOBBYIST_FIRM_EMPLOYER2)
CREATE TABLE IF NOT EXISTS lobbyist_firm_employer2_cd (
    id BIGSERIAL,
    firm_id INTEGER,
    filing_id INTEGER,
    filing_sequence INTEGER,
    firm_name TEXT,
    employer_name TEXT,
    rpt_start TIMESTAMP,
    rpt_end TIMESTAMP,
    per_total NUMERIC(12,2),
    cum_total NUMERIC(12,2),
    lby_actvty TEXT,
    ext_lby_actvty TEXT,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM_HISTORY_CD.TSV  (doc table: LOBBYIST_FIRM_HISTORY)
CREATE TABLE IF NOT EXISTS lobbyist_firm_history_cd (
    id BIGSERIAL,
    contributor_id INTEGER,
    current_qtr_amt NUMERIC(12,2),
    firm_id INTEGER,
    firm_name TEXT,
    qtr_1 NUMERIC(12,2),
    qtr_2 NUMERIC(12,2),
    qtr_3 NUMERIC(12,2),
    qtr_4 NUMERIC(12,2),
    qtr_5 NUMERIC(12,2),
    qtr_6 NUMERIC(12,2),
    qtr_7 NUMERIC(12,2),
    qtr_8 NUMERIC(12,2),
    session_id INTEGER,
    session_total_amt NUMERIC(12,2),
    session_yr_1 INTEGER,
    session_yr_2 INTEGER,
    yr_1_ytd_amt NUMERIC(12,2),
    yr_2_ytd_amt NUMERIC(12,2),
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM_LOBBYIST1_CD.TSV  (doc table: LOBBYIST_FIRM_LOBBYIST1)
CREATE TABLE IF NOT EXISTS lobbyist_firm_lobbyist1_cd (
    id BIGSERIAL,
    lobbyist_id INTEGER,
    firm_id INTEGER,
    lobbyist_last_name TEXT,
    lobbyist_first_name TEXT,
    firm_name TEXT,
    session_id INTEGER,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBYIST_FIRM_LOBBYIST2_CD.TSV  (doc table: LOBBYIST_FIRM_LOBBYIST2)
CREATE TABLE IF NOT EXISTS lobbyist_firm_lobbyist2_cd (
    id BIGSERIAL,
    lobbyist_id INTEGER,
    firm_id INTEGER,
    lobbyist_last_name TEXT,
    lobbyist_first_name TEXT,
    firm_name TEXT,
    session_id INTEGER,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/LOBBY_AMENDMENTS_CD.TSV  (doc table: LOBBY_AMENDMENTS)
CREATE TABLE IF NOT EXISTS lobby_amendments_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    rec_type TEXT,
    form_type TEXT,
    exec_date TIMESTAMP,
    from_date TIMESTAMP,
    thru_date TIMESTAMP,
    add_l_cb TEXT,
    add_l_eff TIMESTAMP,
    a_l_naml TEXT,
    a_l_namf TEXT,
    a_l_namt TEXT,
    a_l_nams TEXT,
    del_l_cb TEXT,
    del_l_eff TIMESTAMP,
    d_l_naml TEXT,
    d_l_namf TEXT,
    d_l_namt TEXT,
    d_l_nams TEXT,
    add_le_cb TEXT,
    add_le_eff TIMESTAMP,
    a_le_naml TEXT,
    a_le_namf TEXT,
    a_le_namt TEXT,
    a_le_nams TEXT,
    del_le_cb TEXT,
    del_le_eff TIMESTAMP,
    d_le_naml TEXT,
    d_le_namf TEXT,
    d_le_namt TEXT,
    d_le_nams TEXT,
    add_lf_cb TEXT,
    add_lf_eff TIMESTAMP,
    a_lf_name TEXT,
    del_lf_cb TEXT,
    del_lf_eff TIMESTAMP,
    d_lf_name TEXT,
    other_cb TEXT,
    other_eff TIMESTAMP,
    other_desc TEXT,
    f606_yes TEXT,
    f606_no TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, rec_type)
);

-- CalAccess/DATA/LOOKUP_CODES_CD.TSV  (doc table: LOOKUP_CODES)
CREATE TABLE IF NOT EXISTS lookup_codes_cd (
    code_type INTEGER,
    code_id INTEGER,
    code_desc TEXT,
    PRIMARY KEY (code_id, code_type)
);

-- CalAccess/DATA/LOTH_CD.TSV  (doc table: LOTH)
CREATE TABLE IF NOT EXISTS loth_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    firm_name TEXT,
    firm_city TEXT,
    firm_st TEXT,
    firm_zip4 TEXT,
    firm_phon TEXT,
    subj_naml TEXT,
    subj_namf TEXT,
    subj_namt TEXT,
    subj_nams TEXT,
    pmt_date TIMESTAMP,
    amount NUMERIC(12,2),
    cum_amt NUMERIC(12,2),
    memo_code TEXT,
    memo_refno TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/LPAY_CD.TSV  (doc table: LPAY)
CREATE TABLE IF NOT EXISTS lpay_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    emplr_naml TEXT,
    emplr_namf TEXT,
    emplr_namt TEXT,
    emplr_nams TEXT,
    emplr_city TEXT,
    emplr_st TEXT,
    emplr_zip4 TEXT,
    emplr_phon TEXT,
    lby_actvty TEXT,
    fees_amt NUMERIC(12,2),
    reimb_amt NUMERIC(12,2),
    advan_amt NUMERIC(12,2),
    advan_dscr TEXT,
    per_total NUMERIC(12,2),
    cum_total NUMERIC(12,2),
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    emplr_id TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/NAMES_CD.TSV  (doc table: NAMES)
CREATE TABLE IF NOT EXISTS names_cd (
    id BIGSERIAL,
    namid INTEGER,
    naml TEXT,
    namf TEXT,
    namt TEXT,
    nams TEXT,
    moniker TEXT,
    moniker_pos INTEGER,
    namm TEXT,
    fullname TEXT,
    naml_search TEXT,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/RCPT_CD.TSV  (doc table: RCPT)
CREATE TABLE IF NOT EXISTS rcpt_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    ctrib_naml TEXT,
    ctrib_namf TEXT,
    ctrib_namt TEXT,
    ctrib_nams TEXT,
    ctrib_city TEXT,
    ctrib_st TEXT,
    ctrib_zip4 TEXT,
    ctrib_emp TEXT,
    ctrib_occ TEXT,
    ctrib_self TEXT,
    tran_type TEXT,
    rcpt_date TIMESTAMP,
    date_thru TIMESTAMP,
    amount NUMERIC(12,2),
    cum_ytd NUMERIC(12,2),
    cum_oth NUMERIC(12,2),
    ctrib_dscr TEXT,
    cmte_id TEXT,
    tres_naml TEXT,
    tres_namf TEXT,
    tres_namt TEXT,
    tres_nams TEXT,
    tres_city TEXT,
    tres_st TEXT,
    tres_zip4 TEXT,
    intr_naml TEXT,
    intr_namf TEXT,
    intr_namt TEXT,
    intr_nams TEXT,
    intr_city TEXT,
    intr_st TEXT,
    intr_zip4 TEXT,
    intr_emp TEXT,
    intr_occ TEXT,
    intr_self TEXT,
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    sup_opp_cd TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    xref_schnm TEXT,
    xref_match TEXT,
    int_rate TEXT,
    intr_cmteid TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);
CREATE INDEX IF NOT EXISTS idx_rcpt_cd_cmte_id ON rcpt_cd (cmte_id);
CREATE INDEX IF NOT EXISTS idx_rcpt_cd_ctrib_naml ON rcpt_cd (ctrib_naml);
CREATE INDEX IF NOT EXISTS idx_rcpt_cd_rcpt_date ON rcpt_cd (rcpt_date);

-- CalAccess/DATA/RECEIVED_FILINGS_CD.TSV  (doc table: RECEIVED_FILINGS)
CREATE TABLE IF NOT EXISTS received_filings_cd (
    id BIGSERIAL,
    filer_id INTEGER,
    filing_file_name TEXT,
    received_date TIMESTAMP,
    filing_directory TEXT,
    filing_id INTEGER,
    form_id TEXT,
    receive_comment TEXT,
    PRIMARY KEY (id)
);

-- CalAccess/DATA/REPORTS_CD.TSV  (doc table: REPORTS)
CREATE TABLE IF NOT EXISTS reports_cd (
    rpt_id INTEGER,
    rpt_name TEXT,
    rpt_desc_ TEXT,
    path TEXT,
    data_object TEXT,
    parms_flg_y_n TEXT,
    rpt_type INTEGER,
    parm_definition INTEGER,
    PRIMARY KEY (rpt_id)
);

-- CalAccess/DATA/S401_CD.TSV  (doc table: S401)
CREATE TABLE IF NOT EXISTS s401_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    agent_naml TEXT,
    agent_namf TEXT,
    agent_namt TEXT,
    agent_nams TEXT,
    payee_naml TEXT,
    payee_namf TEXT,
    payee_namt TEXT,
    payee_nams TEXT,
    payee_city TEXT,
    payee_st TEXT,
    payee_zip4 TEXT,
    amount NUMERIC(12,2),
    aggregate NUMERIC(12,2),
    expn_dscr TEXT,
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    sup_opp_cd TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    bakref_tid TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/S496_CD.TSV  (doc table: S496)
CREATE TABLE IF NOT EXISTS s496_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    amount NUMERIC(12,2),
    exp_date TIMESTAMP,
    expn_dscr TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    date_thru TIMESTAMP,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/S497_CD.TSV  (doc table: S497)
CREATE TABLE IF NOT EXISTS s497_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    enty_naml TEXT,
    enty_namf TEXT,
    enty_namt TEXT,
    enty_nams TEXT,
    enty_city TEXT,
    enty_st TEXT,
    enty_zip4 TEXT,
    ctrib_emp TEXT,
    ctrib_occ TEXT,
    ctrib_self TEXT,
    elec_date TIMESTAMP,
    ctrib_date TIMESTAMP,
    date_thru TIMESTAMP,
    amount NUMERIC(12,2),
    cmte_id TEXT,
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    memo_code TEXT,
    memo_refno TEXT,
    bal_id TEXT,
    cand_id TEXT,
    sup_off_cd TEXT,
    sup_opp_cd TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/S498_CD.TSV  (doc table: S498)
CREATE TABLE IF NOT EXISTS s498_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    tran_id TEXT,
    entity_cd TEXT,
    cmte_id TEXT,
    payor_naml TEXT,
    payor_namf TEXT,
    payor_namt TEXT,
    payor_nams TEXT,
    payor_city TEXT,
    payor_st TEXT,
    payor_zip4 TEXT,
    date_rcvd TIMESTAMP,
    amt_rcvd NUMERIC(12,2),
    cand_naml TEXT,
    cand_namf TEXT,
    cand_namt TEXT,
    cand_nams TEXT,
    office_cd TEXT,
    offic_dscr TEXT,
    juris_cd TEXT,
    juris_dscr TEXT,
    dist_no TEXT,
    off_s_h_cd TEXT,
    bal_name TEXT,
    bal_num TEXT,
    bal_juris TEXT,
    sup_opp_cd TEXT,
    amt_attrib NUMERIC(12,2),
    memo_code TEXT,
    memo_refno TEXT,
    employer TEXT,
    occupation TEXT,
    selfemp_cb TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/SMRY_CD.TSV  (doc table: SMRY)
CREATE TABLE IF NOT EXISTS smry_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item TEXT,
    rec_type TEXT,
    form_type TEXT,
    amount_a NUMERIC(12,2),
    amount_b NUMERIC(12,2),
    amount_c NUMERIC(12,2),
    elec_dt TIMESTAMP,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- CalAccess/DATA/SPLT_CD.TSV  (doc table: SPLT)
CREATE TABLE IF NOT EXISTS splt_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    pform_type TEXT,
    ptran_id TEXT,
    elec_date TIMESTAMP,
    elec_amount NUMERIC(12,2),
    elec_code TEXT,
    PRIMARY KEY (amend_id, filing_id, line_item, pform_type)
);

-- CalAccess/DATA/TEXT_MEMO_CD.TSV  (doc table: TEXT_MEMO)
CREATE TABLE IF NOT EXISTS text_memo_cd (
    filing_id INTEGER,
    amend_id INTEGER,
    line_item INTEGER,
    rec_type TEXT,
    form_type TEXT,
    ref_no TEXT,
    text4000 TEXT,
    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
);

-- ============================================================================
-- Scraper-owned tables (NOT CAL-ACCESS sourced — used by scraper workflows
-- and the filing-deadline MCP tools)
-- ============================================================================
CREATE TABLE IF NOT EXISTS filing_calendar (
    calendar_id SERIAL PRIMARY KEY,
    election_date DATE NOT NULL,
    report_type VARCHAR(50) NOT NULL,
    deadline_date DATE NOT NULL,
    grace_period_days INTEGER DEFAULT 0,
    source_url VARCHAR(500),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS election_results (
    election_id SERIAL PRIMARY KEY,
    election_date DATE NOT NULL,
    election_type VARCHAR(30) NOT NULL,
    jurisdiction VARCHAR(100) NOT NULL,
    sub_jurisdiction VARCHAR(100),
    pdf_url VARCHAR(500),
    pdf_filename VARCHAR(200),
    file_size_bytes BIGINT,
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS entity (
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

CREATE TABLE IF NOT EXISTS entity_alias (
    alias_id BIGSERIAL NOT NULL PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
    alias_name VARCHAR(300) NOT NULL,
    source_filer_id VARCHAR(20),
    source_table VARCHAR(30), -- e.g., 'filername', 'cntrb_cd', 'names_master'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entity_merge_queue (
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

CREATE TABLE IF NOT EXISTS source_info (
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

CREATE TABLE IF NOT EXISTS load_checkpoint (
    checkpoint_id SERIAL NOT NULL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'calaccess',
    file_hash VARCHAR(64) NOT NULL, -- SHA-256 of the file
    source_file VARCHAR(200), -- e.g., 'CalAccess/DATA/RCPT_CD.TSV'
    processed_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rows_processed INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS etl_dead_letter (
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_load_checkpoint_table_hash
    ON load_checkpoint(table_name, source, file_hash);

-- ============================================================================
-- Grant permissions (read-only role for MCP server)
-- ============================================================================

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
-- Query-support indexes
--
-- The SOS export leaves CMTE_ID blank on most detail rows (~87% of rcpt_cd,
-- ~75% of expn_cd). A committee's activity is reliably identified through
-- the filing the row was filed on (detail.filing_id -> filer_filings_cd
-- -> filer_xref_cd.xref_id), so the MCP query tools need fast lookups on
-- the filing-related columns below.
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_rcpt_cd_filing_id ON rcpt_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_expn_cd_filing_id ON expn_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_smry_cd_filing_id ON smry_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_filer_filings_cd_filing_id ON filer_filings_cd (filing_id);
CREATE INDEX IF NOT EXISTS idx_filer_filings_cd_filer_id ON filer_filings_cd (filer_id);
CREATE INDEX IF NOT EXISTS idx_filer_xref_cd_xref_id ON filer_xref_cd (xref_id);
CREATE INDEX IF NOT EXISTS idx_filername_cd_filer_id ON filername_cd (filer_id);

-- ============================================================================
-- End of migration
-- ============================================================================
