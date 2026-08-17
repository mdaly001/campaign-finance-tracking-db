"""Table definitions registry for CAL-ACCESS data sources.

Maps CAL-ACCESS TSV filenames to their table codes, descriptions,
conflict columns, and type coercion hints.

Populated during Step 5a (discovery) and used by Step 6 (etl.py)
for the full-load and incremental-load runners.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TableDefinition:
    """Schema-level definition for one CAL-ACCESS table.

    Attributes:
        code: Short table code (e.g. "RCPT_CD").
        description: Human-readable description.
        category: One of "fact", "dimension", "disclosure", "lobbying", "other".
        tsv_files: List of TSV filenames in dbwebexport.zip.
        conflict_columns: Columns forming the unique key for upsert.
        type_coercions: Optional {column: "numeric"|"date"|"integer"|"timestamp"}.
        required_columns: Columns that must be non-null for a valid row.
        skip_columns: Internal columns to exclude from the insert.
        partition_by_date: If True, the table uses PostgreSQL partitioning.
        date_column: The DATE/TIMESTAMPTZ column used for partitioning.
    """

    code: str
    description: str
    category: str = "other"
    tsv_files: list[str] | None = None
    conflict_columns: list[str] | None = None
    type_coercions: dict[str, str] | None = None
    required_columns: list[str] | None = None
    skip_columns: list[str] | None = None
    partition_by_date: bool = False
    date_column: str | None = None
    source: str | None = None  # "tsv" or "scrape" — data loading method


# ------------------------------------------------------------------ #
#  FACT TABLES  (core financial records)
# ------------------------------------------------------------------ #
TABLE_DEFINITIONS: dict[str, TableDefinition] = {
    "RCPT_CD": TableDefinition(
        code="RCPT_CD",
        description="Receipts — contributions, expenditures, refunds",
        category="fact",
        tsv_files=["RCPT_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "amount": "numeric",
            "receipt_dt": "date",
            "line_item": "integer",
            "election_date": "date",
            "d_c_d_a": "date",
            "d_c_d_b": "date",
            "d_c_d_c": "date",
            "d_c_d_d": "date",
        },
        partition_by_date=True,
        date_column="receipt_dt",
    ),
    "CNTRB_CD": TableDefinition(
        code="CNTRB_CD",
        description="Contributor master — aggregated totals",
        category="fact",
        tsv_files=["CNTRB_CD.tsv"],
        conflict_columns=["ctrib_id"],
        type_coercions={
            "total_gives": "numeric",
            "total_year": "numeric",
        },
    ),
    "EXPPD_CD": TableDefinition(
        code="EXPPD_CD",
        description="Expenditures",
        category="fact",
        tsv_files=["EXPPD_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "amount": "numeric",
            "exppn_date": "date",
            "line_item": "integer",
        },
        partition_by_date=True,
        date_column="exppn_date",
    ),
    "LOANS_CD": TableDefinition(
        code="LOANS_CD",
        description="Loans received/made",
        category="fact",
        tsv_files=["LOANS_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "loan_amt": "numeric",
            "repmt_amt": "numeric",
            "balance_due": "numeric",
            "loan_dt": "date",
            "repmt_dt": "date",
            "interest_rt": "numeric",
            "line_item": "integer",
        },
        partition_by_date=True,
        date_column="loan_dt",
    ),
    "INTTRF_CD": TableDefinition(
        code="INTTRF_CD",
        description="Inter-committee transfers",
        category="fact",
        tsv_files=["INTTRF_CD.tsv"],
        conflict_columns=["tran_id"],
        type_coercions={
            "amount": "numeric",
            "ref_amt": "numeric",
            "tran_dt": "date",
            "ref_dt": "date",
            "line_item": "integer",
        },
    ),
    "DEBT_CD": TableDefinition(
        code="DEBT_CD",
        description="Debts owed",
        category="fact",
        tsv_files=["DEBT_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "beg_bal": "numeric",
            "debts_inc": "numeric",
            "debts_paid": "numeric",
            "end_bal": "numeric",
            "line_item": "integer",
        },
    ),
    "SMRY_CD": TableDefinition(
        code="SMRY_CD",
        description="Filing summary totals",
        category="fact",
        tsv_files=["SMRY_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "amount_a": "numeric",
            "amount_b": "numeric",
            "amount_c": "numeric",
            "line_item": "integer",
            "elec_dt": "date",
        },
    ),
    "SPLT_CD": TableDefinition(
        code="SPLT_CD",
        description="Split records (allocations)",
        category="fact",
        tsv_files=["SPLT_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "elec_amount": "numeric",
            "line_item": "integer",
            "elec_date": "date",
        },
    ),
    "TEXT_MEMO_CD": TableDefinition(
        code="TEXT_MEMO_CD",
        description="Text memo descriptions",
        category="fact",
        tsv_files=["TEXT_MEMO_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "line_item": "integer",
        },
    ),
    # ------------------------------------------------------------------ #
    #  DIMENSION / FILER TABLES
    # ------------------------------------------------------------------ #
    "FILERNAME_CD": TableDefinition(
        code="FILERNAME_CD",
        description="Filer name master (F400 registration names)",
        category="dimension",
        tsv_files=["FILERNAME_CD.tsv"],
        conflict_columns=["xref_filer_id", "filer_id", "effect_dt"],
        type_coercions={
            "cand_dist": "integer",
            "cand_yr": "integer",
            "effect_dt": "date",
        },
    ),
    "ADDRESS_CD": TableDefinition(
        code="ADDRESS_CD",
        description="Address master",
        category="dimension",
        tsv_files=["ADDRESS_CD.tsv"],
        conflict_columns=["adrid"],
    ),
    "FILER_XREF_CD": TableDefinition(
        code="FILER_XREF_CD",
        description="Filer cross-references (mergers, ID changes)",
        category="dimension",
        tsv_files=["FILER_XREF_CD.tsv"],
        conflict_columns=["filer_id", "xref_id", "effect_dt"],
        type_coercions={
            "effect_dt": "date",
        },
    ),
    "FILER_LINKS_CD": TableDefinition(
        code="FILER_LINKS_CD",
        description="Filer relationships (sponsorship, parent/child)",
        category="dimension",
        tsv_files=["FILER_LINKS_CD.tsv"],
        conflict_columns=["filer_id_a", "filer_id_b", "effect_dt"],
        type_coercions={
            "effect_dt": "date",
            "termination_dt": "date",
            "session_id": "integer",
        },
    ),
    "NAMES_CD": TableDefinition(
        code="NAMES_CD",
        description="Entity name master (for fuzzy matching)",
        category="dimension",
        tsv_files=["NAMES_CD.tsv"],
        conflict_columns=["namid"],
        type_coercions={
            "moniker_pos": "integer",
        },
    ),
    # ------------------------------------------------------------------ #
    #  FILING TABLES
    # ------------------------------------------------------------------ #
    "FILINGS_CD": TableDefinition(
        code="FILINGS_CD",
        description="Cover sheets / filings",
        category="dimension",
        tsv_files=["FILINGS_CD.tsv"],
        conflict_columns=["filing_id"],
        type_coercions={
            "filing_date": "date",
            "election_date": "date",
            "rpt_start": "date",
            "rpt_end": "date",
            "rpt_date": "date",
            "session_id": "integer",
        },
    ),
    "FILING_TYPE_CD": TableDefinition(
        code="FILING_TYPE_CD",
        description="Cover sheet filing type codes",
        category="dimension",
        tsv_files=["FILING_TYPE_CD.tsv"],
        conflict_columns=["filing_type"],
    ),
    "FILING_PERIOD_CD": TableDefinition(
        code="FILING_PERIOD_CD",
        description="Filing period definitions",
        category="dimension",
        tsv_files=["FILING_PERIOD_CD.tsv"],
        conflict_columns=["period_id"],
        type_coercions={
            "start_date": "date",
            "end_date": "date",
            "deadline": "date",
        },
    ),
    "HDR_CD": TableDefinition(
        code="HDR_CD",
        description="Header records",
        category="other",
        tsv_files=["HDR_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
    ),
    "HEADER_DEFS_CD": TableDefinition(
        code="HEADER_DEFS_CD",
        description="Form header definitions",
        category="other",
        tsv_files=["HEADER_DEFS_CD.tsv"],
        conflict_columns=["form_id", "line_number", "rec_type"],
        type_coercions={
            "line_number": "integer",
        },
    ),
    # ------------------------------------------------------------------ #
    #  DISCLOSURE REPORTS (CVR)
    # ------------------------------------------------------------------ #
    "CVR_CAMP_DISC": TableDefinition(
        code="CVR_CAMP_DISC",
        description="CVR Campaign Disclosure (F496)",
        category="disclosure",
        tsv_files=["CVR_CAMP_DISC.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "report_num": "integer",
            "rpt_date": "date",
            "from_date": "date",
            "thru_date": "date",
            "elect_date": "date",
            "cash_on_hand": "numeric",
            "total_contributions": "numeric",
            "total_expenditures": "numeric",
            "loans_received": "numeric",
            "loan_repayments": "numeric",
            "other_loans": "numeric",
            "other_payments": "numeric",
            "debts_owed": "numeric",
            "net_change": "numeric",
            "late_rptno": "integer",
        },
    ),
    "CVR_REGISTRATION": TableDefinition(
        code="CVR_REGISTRATION",
        description="CVR Registration (F400)",
        category="disclosure",
        tsv_files=["CVR_REGISTRATION.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "report_num": "integer",
            "rpt_date": "date",
            "ls_beg_yr": "integer",
            "ls_end_yr": "integer",
            "cand_dist": "integer",
            "cand_yr": "integer",
            "filing_sequence": "integer",
            "incrb_dt": "date",
        },
    ),
    "CVR_SO": TableDefinition(
        code="CVR_SO",
        description="CVR Statement of Organization (F460)",
        category="disclosure",
        tsv_files=["CVR_SO.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "report_num": "integer",
            "rpt_date": "date",
            "qualify_dt": "date",
            "term_date": "date",
            "filing_sequence": "integer",
        },
    ),
    "CVR_LOBBY_DISC": TableDefinition(
        code="CVR_LOBBY_DISC",
        description="CVR Lobbying Disclosure (F455)",
        category="disclosure",
        tsv_files=["CVR_LOBBY_DISC.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "report_num": "integer",
            "rpt_date": "date",
            "from_date": "date",
            "thru_date": "date",
            "filing_sequence": "integer",
        },
    ),
    "CVR2_CAMP_DISC": TableDefinition(
        code="CVR2_CAMP_DISC",
        description="CVR2 Campaign Disclosure (Compact)",
        category="disclosure",
        tsv_files=["CVR2_CAMP_DISC.tsv"],
        conflict_columns=["filing_id", "amend_id", "line_item"],
        type_coercions={
            "line_item": "integer",
        },
    ),
    # ------------------------------------------------------------------ #
    #  LOBBYING TABLES
    # ------------------------------------------------------------------ #
    "LEMP_CD": TableDefinition(
        code="LEMP_CD",
        description="Lobbyist Employer Registrations",
        category="lobbying",
        tsv_files=["LEMP_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "rpt_date": "date",
            "from_date": "date",
            "thru_date": "date",
            "fee": "numeric",
            "filing_sequence": "integer",
        },
    ),
    "LACT_CD": TableDefinition(
        code="LACT_CD",
        description="Lobbyist Activity Reports",
        category="lobbying",
        tsv_files=["LACT_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "rpt_date": "date",
            "from_date": "date",
            "thru_date": "date",
            "fee": "numeric",
            "filing_sequence": "integer",
        },
    ),
    "LPAY_CD": TableDefinition(
        code="LPAY_CD",
        description="Lobbyist Payments",
        category="lobbying",
        tsv_files=["LPAY_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "rpt_date": "date",
            "amount": "numeric",
            "filing_sequence": "integer",
        },
    ),
    "LCCM_CD": TableDefinition(
        code="LCCM_CD",
        description="Lobbyist Candidate Contributions",
        category="lobbying",
        tsv_files=["LCCM_CD.tsv"],
        conflict_columns=["filing_id", "amend_id", "rec_type"],
        type_coercions={
            "rpt_date": "date",
            "amount": "numeric",
            "filing_sequence": "integer",
        },
    ),
    # ------------------------------------------------------------------ #
    #  SUPPORTING / REFERENCE TABLES
    # ------------------------------------------------------------------ #
    "ACRONYMS_CD": TableDefinition(
        code="ACRONYMS_CD",
        description="Committee acronym definitions",
        category="other",
        tsv_files=["ACRONYMS_CD.tsv"],
        conflict_columns=["acronym"],
    ),
    "FILER_TYPES_CD": TableDefinition(
        code="FILER_TYPES_CD",
        description="Filer type definitions (PC, CD, LC, OC)",
        category="other",
        tsv_files=["FILER_TYPES_CD.tsv"],
        conflict_columns=["filer_type"],
    ),
    "FILER_STATUS_CD": TableDefinition(
        code="FILER_STATUS_CD",
        description="Filer status type definitions",
        category="other",
        tsv_files=["FILER_STATUS_CD.tsv"],
        conflict_columns=["status_type"],
    ),
    "GROUP_TYPES_CD": TableDefinition(
        code="GROUP_TYPES_CD",
        description="Committee group type definitions",
        category="other",
        tsv_files=["GROUP_TYPES_CD.tsv"],
        conflict_columns=["grp_id"],
    ),
    "REPORT_TYPES_CD": TableDefinition(
        code="REPORT_TYPES_CD",
        description="Report type definitions (F496, F497)",
        category="other",
        tsv_files=["REPORT_TYPES_CD.tsv"],
        conflict_columns=["rpt_id"],
    ),
    "LEGISLATIVE_SESSIONS_CD": TableDefinition(
        code="LEGISLATIVE_SESSIONS_CD",
        description="CA legislative session years",
        category="other",
        tsv_files=["LEGISLATIVE_SESSIONS_CD.tsv"],
        conflict_columns=["session_id"],
        type_coercions={
            "session_id": "integer",
            "begin_date": "date",
            "end_date": "date",
        },
    ),
    "LOOKUP_CODES": TableDefinition(
        code="LOOKUP_CODES",
        description="CAL-ACCESS code definitions",
        category="other",
        tsv_files=["LOOKUP_CODES.tsv"],
        conflict_columns=["code_type", "code_id"],
    ),
    "IMAGE_LINKS_CD": TableDefinition(
        code="IMAGE_LINKS_CD",
        description="Document image links",
        category="other",
        tsv_files=["IMAGE_LINKS_CD.tsv"],
        conflict_columns=["img_link_id"],
        type_coercions={
            "img_dt": "date",
        },
    ),
    "EFS_FILING_LOG": TableDefinition(
        code="EFS_FILING_LOG",
        description="E-filing submission log",
        category="other",
        tsv_files=["EFS_FILING_LOG.tsv"],
        conflict_columns=["filer_id", "filing_date"],
        type_coercions={
            "filing_date": "date",
            "error_no": "integer",
        },
    ),
    "RECEIVED_FILINGS_CD": TableDefinition(
        code="RECEIVED_FILINGS_CD",
        description="SOS receipt tracking",
        category="other",
        tsv_files=["RECEIVED_FILINGS_CD.tsv"],
        conflict_columns=["filer_id", "filing_file_name"],
        type_coercions={
            "received_date": "date",
        },
    ),
    "FILER_TYPE_ASSIGN_CD": TableDefinition(
        code="FILER_TYPE_ASSIGN_CD",
        description="Filer-to-type assignments",
        category="other",
        tsv_files=["FILER_TYPE_ASSIGN_CD.tsv"],
        conflict_columns=["filer_id", "filer_type", "effect_dt"],
        type_coercions={
            "effect_dt": "date",
            "session_id": "integer",
            "nyq_dt": "date",
            "cand_yr": "integer",
        },
    ),
    "FILER_ETHICS_CD": TableDefinition(
        code="FILER_ETHICS_CD",
        description="Ethics class for lobbying filers",
        category="other",
        tsv_files=["FILER_ETHICS_CD.tsv"],
        conflict_columns=["filer_id", "session_id"],
        type_coercions={
            "session_id": "integer",
            "ethics_date": "date",
        },
    ),
    "FILER_INTERESTS_CD": TableDefinition(
        code="FILER_INTERESTS_CD",
        description="Lobbying interest codes per filer",
        category="other",
        tsv_files=["FILER_INTERESTS_CD.tsv"],
        conflict_columns=["filer_id", "session_id", "interest_cd"],
        type_coercions={
            "session_id": "integer",
            "effect_date": "date",
        },
    ),
    "FILER_ACRONYMS_CD": TableDefinition(
        code="FILER_ACRONYMS_CD",
        description="Filer-acronym mappings",
        category="other",
        tsv_files=["FILER_ACRONYMS_CD.tsv"],
        conflict_columns=["acronym", "filer_id"],
    ),
    "FILER_ADDRESS_CD": TableDefinition(
        code="FILER_ADDRESS_CD",
        description="Filer-to-address mapping",
        category="other",
        tsv_files=["FILER_ADDRESS_CD.tsv"],
        conflict_columns=["filer_id", "adrid", "effect_dt"],
        type_coercions={
            "effect_dt": "date",
            "session_id": "integer",
        },
    ),
    # ------------------------------------------------------------------ #
    #  Non-TSV tables (populated via scrapers, not dbwebexport.zip)
    # ------------------------------------------------------------------ #
    "FILING_CALENDAR": TableDefinition(
        code="FILING_CALENDAR",
        description="Filing deadlines cross-referenced with election dates",
        category="dimension",
        tsv_files=[],
        conflict_columns=[],
        source="scrape",
    ),
    "ELECTION_RESULTS": TableDefinition(
        code="ELECTION_RESULTS",
        description="SOS election results PDF discovery metadata",
        category="dimension",
        tsv_files=[],
        conflict_columns=[],
        source="scrape",
    ),
}


def get_table_definitions() -> dict[str, TableDefinition]:
    """Return all registered table definitions."""
    return TABLE_DEFINITIONS


def get_facts() -> list[TableDefinition]:
    """Return only fact tables (financial records)."""
    return [
        v for v in TABLE_DEFINITIONS.values() if v.category == "fact"
    ]


def get_dimensions() -> list[TableDefinition]:
    """Return only dimension/reference tables."""
    return [
        v for v in TABLE_DEFINITIONS.values() if v.category == "dimension"
    ]


def get_all_codes() -> list[str]:
    """Return sorted list of all table codes."""
    return sorted(TABLE_DEFINITIONS.keys())
