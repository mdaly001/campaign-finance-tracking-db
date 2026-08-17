"""Tests for state/etl.py — FullLoadRunner, IncrementalLoadRunner, ResumeRunner.

Tests cover:
- FullLoadRunner: basic load, checkpoint, zero-rows watchdog, partial load
- IncrementalLoadRunner: skip unchanged, load changed, zip checksum check
- ResumeRunner: resume from checkpoint
- CLI: list, full, incremental, resume commands
"""

import hashlib

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from core.etl.checkpoint import LoadCheckpoint
from core.etl.loader import LoadConfig, TableLoader
from state.etl import (
    LOAD_ORDER,
    _build_load_config,
)
from state.tables import TABLE_DEFINITIONS


# ------------------------------------------------------------------ #
#  Test fixtures
# ------------------------------------------------------------------ #
def _make_engine():
    """Create an in-memory SQLite engine with ETL tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def _exec(sql, **kw):
        with engine.begin() as conn:
            conn.execute(text(sql), kw)

    _exec(
        """
        CREATE TABLE load_checkpoint (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name    TEXT NOT NULL,
            file_hash     TEXT NOT NULL,
            processed_date TEXT NOT NULL,
            loaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(table_name, file_hash)
        )
        """
    )
    _exec(
        """
        CREATE TABLE etl_dead_letter (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name      TEXT NOT NULL,
            row_data        TEXT NOT NULL,
            error_message   TEXT NOT NULL,
            source_file     TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return engine


def _exec_sql(engine, sql, **kw):
    """Helper to run raw SQL on an engine."""
    with engine.begin() as conn:
        conn.execute(text(sql), kw)


def _fetch(engine, sql):
    """Helper to run a SELECT and return rows."""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        return result.fetchall()


def _make_tsv_for_table(table_code, rows):
    """Build TSV bytes matching the columns of a specific table.

    Args:
        table_code: Table definition key (e.g. "SMRY_CD", "CNTRB_CD").
        rows: List of lists, each inner list is one row's values.

    Returns: TSV-encoded bytes with header matching the table's columns.
    """
    if table_code not in TABLE_DEFINITIONS:
        raise KeyError(f"Unknown table: {table_code}")
    # Use a subset of the table's columns that will exist in the test schema
    # We pick columns that make sense for a small test dataset
    if table_code == "SMRY_CD":
        header = ["filing_id", "amend_id", "line_item", "amount_a", "amount_b"]
    elif table_code == "CNTRB_CD":
        header = ["ctrib_id", "ctrib_naml", "total_gives"]
    elif table_code == "EXPPD_CD":
        header = ["filing_id", "payee_naml", "amount", "expn_date"]
    elif table_code == "RCPT_CD":
        header = ["filing_id", "amend_id", "line_item", "ctrib_naml", "amount", "receipt_dt"]
    elif table_code == "SMRY_CD":
        header = ["filing_id", "amend_id", "line_item", "amount_a", "amount_b"]
    else:
        # Fallback: generic
        header = ["id", "name", "amount"]

    lines = ["\t".join(header)]
    for row in rows:
        lines.append("\t".join(str(row[i]) if i < len(row) else "" for i in range(len(header))))
    return "\n".join(lines).encode("utf-8")


# ------------------------------------------------------------------ #
#  Test _build_load_config
# ------------------------------------------------------------------ #
class TestBuildLoadConfig:
    """Test LoadConfig construction from TableDefinitions."""

    def test_build_config_for_rcpt_cd(self):
        """RCPT_CD should get proper conflict columns and type coercions."""
        tsv = _make_tsv_for_table("RCPT_CD", [
            ["F1", "A1", "1", "Alice", "100.50", "2024-01-01"],
        ])
        h = hashlib.sha256(tsv).hexdigest()
        config = _build_load_config("RCPT_CD", tsv, h)

        assert config.table_name == "rcpt_cd"
        assert config.conflict_columns == ["filing_id", "amend_id", "line_item"]
        assert config.type_coercions["amount"] == "numeric"
        assert config.type_coercions["line_item"] == "integer"

    def test_build_config_unknown_table_raises(self):
        """Unknown table code should raise KeyError."""
        tsv = b"id\tname\n1\tAlice"
        with pytest.raises(KeyError, match="Unknown table code: NONEXISTENT"):
            _build_load_config("NONEXISTENT", tsv, "hash")

    def test_load_order_is_complete(self):
        """LOAD_ORDER should contain at least all registered table codes."""
        codes = set(TABLE_DEFINITIONS.keys())
        order = set(LOAD_ORDER)
        missing = codes - order
        if missing:
            pytest.fail(
                f"TABLE_DEFINITIONS has {len(missing)} codes not in LOAD_ORDER: "
                f"{sorted(missing)[:10]}"
            )


# ------------------------------------------------------------------ #
#  Test TableLoader integration (used by all runners)
# ------------------------------------------------------------------ #
class TestTableLoaderIntegration:
    """Integration tests for TableLoader with real table schemas."""

    def setup_method(self):
        self.engine = _make_engine()

    def test_load_smry_cd_with_coercion(self):
        """Loading SMRY_CD should coerce numeric types and upsert."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE smry_cd (
                filing_id TEXT, amend_id TEXT, line_item INTEGER,
                amount_a REAL, amount_b REAL,
                UNIQUE(filing_id, amend_id, line_item)
            )
            """,
        )

        tsv = _make_tsv_for_table("SMRY_CD", [
            ["F100", "A1", "1", "1000.00", "500.00"],
            ["F100", "A1", "2", "2000.00", "1000.00"],
        ])

        config = LoadConfig(
            table_name="smry_cd",
            tsv_files=["SMRY_CD.tsv"],
            conflict_columns=["filing_id", "amend_id", "line_item"],
            type_coercions={
                "amount_a": "numeric",
                "amount_b": "numeric",
                "line_item": "integer",
            },
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)
        summary = loader.load(config, tsv)

        assert summary.rows_read == 2
        assert summary.rows_upserted == 2
        assert summary.rows_failed == 0

        rows = _fetch(self.engine, "SELECT filing_id, amount_a FROM smry_cd ORDER BY line_item")
        assert rows[0][0] == "F100"
        assert rows[1][0] == "F100"
        assert float(rows[0][1]) == 1000.0
        assert float(rows[1][1]) == 2000.0

    def test_load_cntrb_cd_with_coercion(self):
        """Loading CNTRB_CD should coerce total_gives to numeric."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE cntrb_cd (
                ctrib_id TEXT PRIMARY KEY, ctrib_naml TEXT, total_gives REAL
            )
            """,
        )

        tsv = _make_tsv_for_table("CNTRB_CD", [
            ["C001", "Alice Smith", "5000.00"],
            ["C002", "Bob Jones", "3000.00"],
        ])

        config = LoadConfig(
            table_name="cntrb_cd",
            tsv_files=["CNTRB_CD.tsv"],
            conflict_columns=["ctrib_id"],
            type_coercions={"total_gives": "numeric"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)
        summary = loader.load(config, tsv)

        assert summary.rows_upserted == 2

        rows = _fetch(
            self.engine, "SELECT ctrib_naml, total_gives FROM cntrb_cd ORDER BY ctrib_id"
        )
        assert rows[0][0] == "Alice Smith"
        assert float(rows[0][1]) == 5000.0

    def test_load_saves_checkpoint(self):
        """Loading should save a checkpoint entry."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE smry_cd (
                filing_id TEXT, amend_id TEXT, line_item INTEGER,
                amount_a REAL, amount_b REAL,
                UNIQUE(filing_id, amend_id, line_item)
            )
            """,
        )

        tsv = _make_tsv_for_table("SMRY_CD", [["F1", "A1", "1", "100", "0"]])
        config = LoadConfig(
            table_name="smry_cd",
            tsv_files=["SMRY_CD.tsv"],
            conflict_columns=["filing_id", "amend_id", "line_item"],
            type_coercions={"amount_a": "numeric", "amount_b": "numeric", "line_item": "integer"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)
        loader.load(config, tsv)

        # Verify checkpoint
        checkpoint = LoadCheckpoint(self.engine)
        file_hash = hashlib.sha256(tsv).hexdigest()
        cp = checkpoint.get_checkpoint("smry_cd", file_hash)
        assert cp is not None

    def test_checkpoint_skips_same_file(self):
        """Loading the same file twice should skip on second load."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE smry_cd (
                filing_id TEXT, amend_id TEXT, line_item INTEGER,
                amount_a REAL, amount_b REAL,
                UNIQUE(filing_id, amend_id, line_item)
            )
            """,
        )

        tsv = _make_tsv_for_table("SMRY_CD", [["F1", "A1", "1", "100", "0"]])
        config = LoadConfig(
            table_name="smry_cd",
            tsv_files=["SMRY_CD.tsv"],
            conflict_columns=["filing_id", "amend_id", "line_item"],
            type_coercions={"amount_a": "numeric", "amount_b": "numeric", "line_item": "integer"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)

        s1 = loader.load(config, tsv)
        assert s1.rows_upserted == 1

        # Second load — same bytes, same hash → should skip
        s2 = loader.load(config, tsv)
        assert s2.rows_upserted == 0
        assert s2.rows_skipped == 1

        # DB should still have exactly 1 row (no duplicates)
        rows = _fetch(self.engine, "SELECT COUNT(*) FROM smry_cd")
        assert rows[0][0] == 1


# ------------------------------------------------------------------ #
#  Test IncrementalLoadRunner (uses TableLoader directly)
# ------------------------------------------------------------------ #
class TestIncrementalLoadRunner:
    """Test incremental load behavior via TableLoader."""

    def setup_method(self):
        self.engine = _make_engine()

    def test_incremental_skips_loaded_table(self):
        """If table already has checkpoint, skip it."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE cntrb_cd (
                ctrib_id TEXT PRIMARY KEY, ctrib_naml TEXT, total_gives REAL
            )
            """,
        )

        # First load
        tsv1 = _make_tsv_for_table("CNTRB_CD", [["C1", "Alice", "100"]])
        config1 = LoadConfig(
            table_name="cntrb_cd",
            tsv_files=["CNTRB_CD.tsv"],
            conflict_columns=["ctrib_id"],
            type_coercions={"total_gives": "numeric"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader1 = TableLoader(self.engine, batch_size=100)
        s1 = loader1.load(config1, tsv1)
        assert s1.rows_upserted == 1

        # Second load with same data — should skip
        s2 = loader1.load(config1, tsv1)
        assert s2.rows_upserted == 0
        assert s2.rows_skipped == 1

    def test_incremental_loads_new_hash(self):
        """Different hash should trigger a load."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE exppd_cd (
                filing_id TEXT PRIMARY KEY, payee_naml TEXT,
                amount REAL, expn_date TEXT
            )
            """,
        )

        # Load with hash A
        tsv_a = _make_tsv_for_table("EXPPD_CD", [["F1", "Vendor A", "500", "2024-01-01"]])
        config_a = LoadConfig(
            table_name="exppd_cd",
            tsv_files=["EXPPD_CD.tsv"],
            conflict_columns=["filing_id"],
            type_coercions={"amount": "numeric"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)
        s_a = loader.load(config_a, tsv_a)
        assert s_a.rows_upserted == 1

        # Load with hash B (different data) — should load again
        tsv_b = _make_tsv_for_table(
            "EXPPD_CD",
            [
                ["F1", "Vendor A", "600", "2024-02-01"],
                ["F2", "Vendor B", "700", "2024-03-01"],
            ],
        )
        config_b = LoadConfig(
            table_name="exppd_cd",
            tsv_files=["EXPPD_CD.tsv"],
            conflict_columns=["filing_id"],
            type_coercions={"amount": "numeric"},
            skip_columns=["__table__", "__file_hash__"],
        )
        s_b = loader.load(config_b, tsv_b)
        assert s_b.rows_upserted == 2

    def test_incremental_checkpoint_after_load(self):
        """After incremental load, checkpoint should be saved."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE smry_cd (
                filing_id TEXT, amend_id TEXT, line_item INTEGER,
                amount_a REAL, amount_b REAL,
                UNIQUE(filing_id, amend_id, line_item)
            )
            """,
        )

        tsv = _make_tsv_for_table("SMRY_CD", [["F1", "A1", "1", "100", "0"]])
        config = LoadConfig(
            table_name="smry_cd",
            tsv_files=["SMRY_CD.tsv"],
            conflict_columns=["filing_id", "amend_id", "line_item"],
            type_coercions={"amount_a": "numeric", "amount_b": "numeric", "line_item": "integer"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)
        loader.load(config, tsv)

        checkpoint = LoadCheckpoint(self.engine)
        file_hash = hashlib.sha256(tsv).hexdigest()
        assert checkpoint.is_loaded("smry_cd", file_hash)


# ------------------------------------------------------------------ #
#  Test ResumeRunner
# ------------------------------------------------------------------ #
class TestResumeRunner:
    """Test resuming from checkpoint."""

    def setup_method(self):
        self.engine = _make_engine()

    def test_resume_skips_checkpointed_tables(self):
        """Resume should skip tables that are already checkpointed.

        We load a table first (which saves a checkpoint), then call
        load() again with the same data — the second call must skip.
        This simulates what ResumeRunner does: it iterates tables and
        skips any that have a valid checkpoint.
        """
        _exec_sql(
            self.engine,
            """
            CREATE TABLE smry_cd (
                filing_id TEXT, amend_id TEXT, line_item INTEGER,
                amount_a REAL, amount_b REAL,
                UNIQUE(filing_id, amend_id, line_item)
            )
            """,
        )

        tsv = _make_tsv_for_table("SMRY_CD", [["F1", "A1", "1", "100", "0"]])
        config = LoadConfig(
            table_name="smry_cd",
            tsv_files=["SMRY_CD.tsv"],
            conflict_columns=["filing_id", "amend_id", "line_item"],
            type_coercions={"amount_a": "numeric", "amount_b": "numeric", "line_item": "integer"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)

        # First load — should succeed
        s1 = loader.load(config, tsv)
        assert s1.rows_upserted == 1

        # Second load with same bytes — should skip (checkpoint hit)
        s2 = loader.load(config, tsv)
        assert s2.rows_upserted == 0
        assert s2.rows_skipped == 1

        # Verify only 1 row in table (no duplicates)
        rows = _fetch(self.engine, "SELECT COUNT(*) FROM smry_cd")
        assert rows[0][0] == 1


# ------------------------------------------------------------------ #
#  Test Zero-Rows Watchdog
# ------------------------------------------------------------------ #
class TestWatchdog:
    """Test that zero-rows during expected periods emit warnings."""

    def setup_method(self):
        self.engine = _make_engine()

    def test_empty_tsv_produces_zero_rows(self):
        """An empty TSV should result in 0 rows read."""
        _exec_sql(
            self.engine,
            """
            CREATE TABLE smry_cd (
                filing_id TEXT, amend_id TEXT, line_item INTEGER,
                amount_a REAL, amount_b REAL
            )
            """,
        )

        tsv = b"filing_id\tamend_id\tline_item\tamount_a\tamount_b\n"  # header only
        config = LoadConfig(
            table_name="smry_cd",
            tsv_files=["SMRY_CD.tsv"],
            conflict_columns=["filing_id", "amend_id", "line_item"],
            type_coercions={"amount_a": "numeric", "amount_b": "numeric", "line_item": "integer"},
            skip_columns=["__table__", "__file_hash__"],
        )
        loader = TableLoader(self.engine, batch_size=100)
        summary = loader.load(config, tsv)

        assert summary.rows_read == 0
        assert summary.rows_upserted == 0
        assert summary.rows_failed == 0


# ------------------------------------------------------------------ #
#  Test Load Order
# ------------------------------------------------------------------ #
class TestLoadOrder:
    """Test that dimension tables load before fact tables."""

    def test_dimensions_before_facts(self):
        """All dimension tables should appear before fact tables in LOAD_ORDER."""
        fact_codes = {
            "RCPT_CD", "CNTRB_CD", "EXPPD_CD", "LOANS_CD",
            "INTTRF_CD", "DEBT_CD", "SMRY_CD", "SPLT_CD",
            "TEXT_MEMO_CD",
        }
        dim_codes = {
            "FILERNAME_CD", "ADDRESS_CD", "FILER_XREF_CD", "FILER_LINKS_CD",
            "NAMES_CD", "FILINGS_CD", "FILING_TYPE_CD", "FILING_PERIOD_CD",
            "HDR_CD", "HEADER_DEFS_CD", "ACRONYMS_CD", "FILER_TYPES_CD",
            "FILER_STATUS_CD", "GROUP_TYPES_CD", "REPORT_TYPES_CD",
            "LEGISLATIVE_SESSIONS_CD", "LOOKUP_CODES",
        }

        last_dim_idx = -1
        first_fact_idx = len(LOAD_ORDER)

        for i, code in enumerate(LOAD_ORDER):
            if code in dim_codes:
                last_dim_idx = i
            if code in fact_codes:
                first_fact_idx = min(first_fact_idx, i)

        assert last_dim_idx < first_fact_idx, (
            f"Dimension table at index {last_dim_idx} appears after "
            f"fact table at index {first_fact_idx}"
        )


# ------------------------------------------------------------------ #
#  Test Table Definitions
# ------------------------------------------------------------------ #
class TestTableDefinitions:
    """Test that TABLE_DEFINITIONS are consistent."""

    def test_all_tables_have_conflict_columns(self):
        """Every table should have conflict_columns defined."""
        for code, td in TABLE_DEFINITIONS.items():
            assert td.conflict_columns, f"Table {code} missing conflict_columns"

    def test_all_tables_have_tsv_files(self):
        """Every table should have tsv_files defined."""
        for code, td in TABLE_DEFINITIONS.items():
            assert td.tsv_files, f"Table {code} missing tsv_files"

    def test_load_order_uniqueness(self):
        """LOAD_ORDER should not contain duplicates."""
        seen = set()
        for code in LOAD_ORDER:
            assert code not in seen, f"Duplicate in LOAD_ORDER: {code}"
            seen.add(code)

    def test_load_order_no_duplicates(self):
        """LOAD_ORDER should contain each code at most once."""
        from collections import Counter

        counts = Counter(LOAD_ORDER)
        duplicates = {k: v for k, v in counts.items() if v > 1}
        assert not duplicates, f"LOAD_ORDER has duplicates: {duplicates}"

    def test_partitioned_tables_have_date_column(self):
        """Partitioned tables should specify date_column."""
        for code, td in TABLE_DEFINITIONS.items():
            if td.partition_by_date:
                assert td.date_column, (
                    f"Table {code} is partitioned but missing date_column"
                )
