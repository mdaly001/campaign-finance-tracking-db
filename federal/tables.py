"""Table definitions registry for FEC federal data sources.

Maps FEC bulk files to their table codes, descriptions, conflict columns,
and type coercions.

Verified against live FEC bulk files (2026-09-21):
  - cm.txt: 15 fields, committee master
  - cn.txt: 15 fields, candidate master
  - ccl.txt: 7 fields, candidate-committee linkage
  - itcont: 21 fields, individual contributions
  - itoth: similar fields, other contributions
  - oppexp: independent expenditures
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TableDefinition:
    """Schema-level definition for one FEC table.

    Attributes:
        code: Short table code (e.g. "FEC_INDIV").
        description: Human-readable description.
        table_name: Actual Postgres table name (e.g. "fec.fec_committees").
        category: One of "fact", "dimension", "disclosure", "lobbying", "other".
        source_file: Source file pattern (e.g. "indiv{yy}.zip").
        source_member: Zip member name (e.g. "itcont").
        conflict_columns: Columns forming the unique key for upsert.
        type_coercions: Optional {column: "numeric"|"date"|"integer"|"timestamp"}.
        required_columns: Columns that must be non-null for a valid row.
        skip_columns: Columns to exclude from the insert.
        cycle_scoped: If True, the table is partitioned by cycle.
        field_count: Expected number of fields per row (for validation).
        column_names: Ordered list of column names (FEC files have no headers).
    """

    code: str
    description: str
    table_name: str = ""
    category: str = "other"
    source_file: str | None = None
    source_member: str | None = None
    conflict_columns: list[str] | None = None
    type_coercions: dict[str, str] | None = None
    required_columns: list[str] | None = None
    skip_columns: list[str] | None = None
    cycle_scoped: bool = False
    field_count: int | None = None
    column_names: list[str] | None = None


# Load order — dimensions before facts
LOAD_ORDER: list[str] = [
    # Master/reference tables first
    "FEC_CM",       # Committee master (cm.txt)
    "FEC_CN",       # Candidate master (cn.txt)
    "FEC_CCL",      # Candidate-committee linkage (ccl.txt)
    # Fact tables
    "FEC_INDIV",    # Individual contributions (itcont)
    "FEC_OTH",      # Other contributions (itoth)
    "FEC_PAS2",     # Conduit contributions (itpas2)
    "FEC_OPPEXP",   # Independent expenditures (oppexp)
]


# FEC column name mappings (files have no headers; positions are fixed)
# FEC_CM: 14 data fields (field 15 is trailing/empty)
FEC_CM_COLUMNS = [
    "committee_id", "committee_name", "treasurer_name", "street_1", "street_2",
    "city", "state", "zip", "organization_type", "committee_type",
    "type_designation", "connected_organization_name", "affiliation",
    "fec_election_year",
]

# FEC_CN: 15 fields
FEC_CN_COLUMNS = [
    "candidate_id", "candidate_name", "party", "cycle", "office", "state",
    "district", "incumbent_status", "candidate_status", "principal_committee_id",
    "pcc_street_1", "pcc_street_2", "pcc_city", "pcc_state", "pcc_zip",
]

# FEC_CCL: 7 fields
FEC_CCL_COLUMNS = [
    "committee_id", "start_year", "end_year", "cand_id", "cand_office",
    "cand_state", "cand_party",
]

# FEC_INDIV / FEC_OTH: 21 fields — itcont.txt and itoth.txt share an IDENTICAL
# record layout (verified field-by-field against both 2024-cycle files, across
# regular, earmarked, and transfer rows).
# Field evidence:
#   2:  'M9'/'M10'/'Q3'/'12P'  -> report_type (monthly/quarterly filer code)
#   3:  'P2024'/'G2024'/'P'     -> election_code
#   5:  '15'/'15E'/'10J'/'18G' -> transaction_type_code (FEC codes)
#   15: ''/'C00401224'          -> other_committee_id (empty on plain receipts;
#                                  populated on earmarks / transfers)
#   16: 'SA11AI.5012' / number  -> memo_code
#   18: 'X' / ''                -> back_reference flag
#   19: 'PAYROLL DEDUCTION...'  -> short_description (free text)
# NOTE: there is NO conduit_code / aggregate_year_to_date / committee_fec_id in
# these files. The previous FEC_CONTRIB_COLUMNS mapping mislabeled positions
# 2/3/5/15/18/19 and cast committee IDs into a numeric column.
FEC_RECEIPT_COLUMNS = [
    "committee_id", "amendment_indicator", "report_type", "election_code",
    "transaction_id", "transaction_type_code", "contributor_type_code",
    "contributor_name", "contributor_city", "contributor_state", "contributor_zip",
    "contributor_employer", "contributor_occupation", "contribution_date",
    "contribution_amount", "other_committee_id", "memo_code", "memo_reference",
    "back_reference", "short_description", "receipt_no",
]

# FEC_OPPEXP: 19 fields (Schedule E independent expenditures)
# Fields: committee_id, amendment_indicator, election_year, report_period,
# transaction_id, transaction_purpose_code, report_type, transaction_type_code,
# payee_name, payee_city, payee_state, payee_zip, expenditure_date, expenditure_amount,
# election_code, description, purpose, memo_code, memo_reference
FEC_OPPEXP_COLUMNS = [
    "committee_id", "amendment_indicator", "election_year", "report_period",
    "transaction_id", "transaction_purpose_code", "report_type", "transaction_type_code",
    "payee_name", "payee_city", "payee_state", "payee_zip", "expenditure_date",
    "expenditure_amount", "election_code", "description", "purpose", "memo_code",
    "memo_reference",
]


FEC_TABLE_DEFINITIONS: dict[str, TableDefinition] = {
    "FEC_CM": TableDefinition(
        code="FEC_CM",
        description="Committee master (cm.txt) — all FEC committees",
        table_name="fec.fec_committees",
        category="dimension",
        source_file="cm{yy}.zip",
        source_member="cm.txt",
        conflict_columns=["committee_id"],
        type_coercions={},
        required_columns=["committee_id"],
        field_count=15,
        column_names=FEC_CM_COLUMNS,
    ),
    "FEC_CN": TableDefinition(
        code="FEC_CN",
        description="Candidate master (cn.txt) — all FEC candidates",
        table_name="fec.fec_candidates",
        category="dimension",
        source_file="cn{yy}.zip",
        source_member="cn.txt",
        conflict_columns=["candidate_id"],
        type_coercions={},
        required_columns=["candidate_id"],
        field_count=15,
        column_names=FEC_CN_COLUMNS,
    ),
    "FEC_CCL": TableDefinition(
        code="FEC_CCL",
        description="Candidate-committee linkage (ccl.txt)",
        table_name="fec.fec_candidate_pac_linkage",
        category="dimension",
        source_file="ccl{yy}.zip",
        source_member="ccl.txt",
        conflict_columns=["committee_id", "cand_id", "start_year"],
        type_coercions={},
        required_columns=["committee_id"],
        field_count=7,
        column_names=FEC_CCL_COLUMNS,
    ),
    "FEC_INDIV": TableDefinition(
        code="FEC_INDIV",
        description="Individual contributions (itcont) — Schedule A",
        table_name="fec.fec_individual_contributions",
        category="fact",
        source_file="indiv{yy}.zip",
        source_member="itcont.txt",
        # receipt_no is globally unique per source row; transaction_id is only
        # unique per report, so keying on it collapses multi-line receipts
        # (~49% of itcont rows share a (committee_id, transaction_id)).
        conflict_columns=["two_year_transaction_period", "committee_id", "receipt_no"],
        type_coercions={
            "contribution_date": "date",
            "contribution_amount": "numeric",
        },
        required_columns=["committee_id", "transaction_id"],
        cycle_scoped=True,
        field_count=21,
        column_names=FEC_RECEIPT_COLUMNS,
    ),
    "FEC_OTH": TableDefinition(
        code="FEC_OTH",
        description="Other contributions (itoth) — Schedule B (PACs, orgs, parties)",
        table_name="fec.fec_other_contributions",
        category="fact",
        source_file="oth{yy}.zip",
        source_member="itoth.txt",
        # receipt_no is globally unique per source row (transaction_id is not).
        conflict_columns=["two_year_transaction_period", "committee_id", "receipt_no"],
        type_coercions={
            "contribution_date": "date",
            "contribution_amount": "numeric",
        },
        required_columns=["committee_id", "transaction_id"],
        cycle_scoped=True,
        field_count=21,
        column_names=FEC_RECEIPT_COLUMNS,
    ),
    "FEC_PAS2": TableDefinition(
        code="FEC_PAS2",
        description="Conduit/earmark contributions (itpas2) — Schedule C",
        table_name="fec.fec_conduit_contributions",
        category="fact",
        source_file="pas2{yy}.zip",
        source_member="itpas2.txt",
        # receipt_no is globally unique per source row (transaction_id is not).
        conflict_columns=["two_year_transaction_period", "committee_id", "receipt_no"],
        type_coercions={
            "contribution_date": "date",
            "contribution_amount": "numeric",
        },
        required_columns=["committee_id", "transaction_id"],
        cycle_scoped=True,
        field_count=22,
        skip_columns=[],
        # Verified against live itpas2.txt (2024 cycle): 22 pipe-delimited fields
        column_names=[
            "committee_id", "amendment_indicator", "report_type", "election_year",
            "transaction_id", "transaction_purpose_code", "contributor_type_code",
            "contributor_name", "contributor_city", "contributor_state", "contributor_zip",
            "contributor_employer", "contributor_occupation", "contribution_date",
            "contribution_amount", "conduit_committee_id", "conduit_candidate_id",
            "line_number", "image_number", "memo_code", "memo_reference", "receipt_no",
        ],
    ),
    "FEC_OPPEXP": TableDefinition(
        code="FEC_OPPEXP",
        description="Independent expenditures & opposition research (oppexp)",
        table_name="fec.fec_independent_expenditures",
        category="fact",
        source_file="oppexp{yy}.zip",
        source_member="oppexp.txt",
        # No fully-unique business key in oppexp.txt; (committee_id,
        # transaction_id, transaction_unique_id) preserves 99.87% of rows
        # (2,943 residual collisions resolved last-wins).
        conflict_columns=["two_year_transaction_period", "committee_id", "transaction_id", "transaction_unique_id"],
        type_coercions={
            "expenditure_date": "date",
            "expenditure_amount": "numeric",
        },
        required_columns=["committee_id", "transaction_id"],
        cycle_scoped=True,
        field_count=26,
        skip_columns=[],
        # Verified against live oppexp.txt (2024 cycle): 26 pipe-delimited fields.
        # Note: expenditure_date uses MM/DD/YYYY format (loader handles it).
        column_names=[
            "committee_id", "amendment_indicator", "election_year", "report_type",
            "transaction_id", "transaction_code", "form_type", "schedule_type",
            "payee_name", "payee_city", "payee_state", "payee_zip",
            "expenditure_date", "expenditure_amount", "election_code", "description",
            "purpose", "category_description", "loan_check_amount", "loan_check_date",
            "payee_type", "back_reference_transaction_id", "image_number",
            "transaction_unique_id", "file_number", "back_reference_id",
        ],
    ),
}
