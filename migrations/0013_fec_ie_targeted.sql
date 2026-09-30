-- 0013: Candidate-targeted independent expenditures (Schedule E)
-- Source: FEC bulk "independent_expenditure_{cycle}.csv" (comma-delimited, with header).
-- This is DISTINCT from fec.fec_independent_expenditures (oppexp.txt = Schedule B
-- operating/opposition-research payments, which carry NO candidate targeting).
-- Here every row is an independent expenditure attributed to a specific federal
-- candidate, with an explicit support/oppose flag.

CREATE SCHEMA IF NOT EXISTS fec;

CREATE TABLE IF NOT EXISTS fec.fec_ie_targeted (
    ie_targeted_id        BIGSERIAL,
    spender_id            TEXT NOT NULL,          -- spe_id: committee making the IE
    spender_name          TEXT,                  -- spe_nam
    candidate_id          TEXT,                  -- cand_id
    candidate_name        TEXT,                  -- cand_name
    election_type         TEXT,                  -- ele_type (P/G/...)
    office_state          TEXT,                  -- can_office_state
    office_district       TEXT,                  -- can_office_dis
    office                TEXT,                  -- can_office (H/S/P)
    party_affiliation     TEXT,                  -- cand_pty_aff
    expenditure_amount    NUMERIC(14,2),        -- exp_amo
    expenditure_date      DATE,                 -- exp_date
    aggregate_amount      NUMERIC(14,2),        -- agg_amo
    support_oppose        TEXT,                  -- sup_opp: S=support, O=oppose
    purpose               TEXT,                  -- pur
    payee                 TEXT,                  -- pay (media vendor)
    file_number           TEXT NOT NULL,         -- file_num (the filing)
    amendment_indicator   TEXT,                 -- amndt_ind
    transaction_id        TEXT NOT NULL,         -- tran_id (line within filing)
    image_number          TEXT,                 -- image_num
    receipt_date          DATE,                 -- receipt_dat
    election_year         INTEGER,               -- fec_election_yr
    previous_file_number  TEXT,                 -- prev_file_num
    dissemination_date    DATE,                 -- dissem_dt
    src_file              TEXT,
    load_ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_fec_ie_targeted_key
        UNIQUE (spender_id, file_number, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_fec_ie_targeted_cand
    ON fec.fec_ie_targeted (candidate_id);
CREATE INDEX IF NOT EXISTS idx_fec_ie_targeted_expdate
    ON fec.fec_ie_targeted (expenditure_date);
CREATE INDEX IF NOT EXISTS idx_fec_ie_targeted_state_dist
    ON fec.fec_ie_targeted (office_state, office_district);

COMMENT ON TABLE fec.fec_ie_targeted IS
    'Candidate-targeted independent expenditures (Schedule E), from FEC independent_expenditure_{cycle}.csv. support_oppose: S=for, O=against.';
