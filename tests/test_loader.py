"""Tests for core/etl/loader.py — TableLoader class."""

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from core.etl.loader import LoadConfig, TableLoader


def _make_engine():
    """Create an in-memory SQLite engine with the required tables."""
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
    """Helper to run raw SQL on an engine (SQLAlchemy 2.0)."""
    with engine.begin() as conn:
        conn.execute(text(sql), kw)


def _fetch(engine, sql):
    """Helper to run a SELECT and return rows."""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        return result.fetchall()


def _make_tsv(rows, has_header=True):
    """Build TSV bytes from a list of row lists."""
    header = "\t".join(["id", "name", "amount"])
    lines = [header] + ["\t".join(row) for row in rows]
    return "\n".join(lines).encode("utf-8")


class TestTableLoaderBasic:
    """Basic load flow tests."""

    def setup_method(self):
        self.engine = _make_engine()
        self.loader = TableLoader(self.engine, batch_size=3)

    def test_load_small_file(self):
        """A small file with 5 rows should upsert all 5."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100.0"],
                ["2", "Bob", "200.0"],
                ["3", "Charlie", "300.0"],
                ["4", "Diana", "400.0"],
                ["5", "Eve", "500.0"],
            ]
        )

        config = LoadConfig(
            table_name="test_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        summary = self.loader.load(config, tsv)

        assert summary.rows_read == 5
        assert summary.rows_upserted == 5
        assert summary.rows_skipped == 0
        assert summary.rows_failed == 0

        rows = _fetch(self.engine, "SELECT COUNT(*) FROM test_table")
        assert rows[0][0] == 5

    def test_load_empty_file(self):
        """An empty TSV should produce a summary with 0 rows."""
        config = LoadConfig(
            table_name="empty_table2",
            tsv_files=["empty.tsv"],
            conflict_columns=["id"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE empty_table2 (id INTEGER PRIMARY KEY, name TEXT)",
        )

        summary = self.loader.load(config, b"id\tname\n")
        assert summary.rows_read == 0
        assert summary.rows_upserted == 0


class TestTypeCoercion:
    """Test type coercion logic."""

    def setup_method(self):
        self.engine = _make_engine()
        self.loader = TableLoader(self.engine, batch_size=100)

    def test_numeric_coercion(self):
        """String amounts should become floats."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100.50"],
                ["2", "Bob", "200.75"],
            ]
        )

        config = LoadConfig(
            table_name="coerce_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            type_coercions={"amount": "numeric"},
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE coerce_table (id INTEGER PRIMARY KEY, name TEXT, amount REAL)",
        )

        summary = self.loader.load(config, tsv)

        assert summary.rows_upserted == 2

        rows = _fetch(self.engine, "SELECT amount FROM coerce_table ORDER BY id")
        assert float(rows[0][0]) == 100.5
        assert float(rows[1][0]) == 200.75

    def test_integer_coercion(self):
        """String IDs should become integers."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100"],
                ["2", "Bob", "200"],
            ]
        )

        config = LoadConfig(
            table_name="int_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            type_coercions={"id": "integer"},
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE int_table (id INTEGER PRIMARY KEY, name TEXT, amount INTEGER)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2

        rows = _fetch(self.engine, "SELECT id FROM int_table ORDER BY id")
        assert rows[0][0] == 1
        assert rows[1][0] == 2

    def test_date_coercion(self):
        """String dates should become date objects."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "2024-01-15"],
                ["2", "Bob", "2024-02-20"],
            ]
        )

        config = LoadConfig(
            table_name="date_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            type_coercions={"amount": "date"},
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE date_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2

    def test_timestamp_coercion(self):
        """String timestamps should become datetime objects."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "2024-01-15 10:30:00"],
                ["2", "Bob", "2024-02-20 14:45:00"],
            ]
        )

        config = LoadConfig(
            table_name="ts_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            type_coercions={"amount": "timestamp"},
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE ts_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2

    def test_coercion_failure_nulls_out(self):
        """Invalid numeric strings should become None, not crash."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "not_a_number"],
                ["2", "Bob", "200"],
            ]
        )

        config = LoadConfig(
            table_name="bad_num_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            type_coercions={"amount": "numeric"},
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE bad_num_table (id INTEGER PRIMARY KEY, name TEXT, amount REAL)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2


class TestRequiredColumnValidation:
    """Test required column validation logic."""

    def setup_method(self):
        self.engine = _make_engine()
        self.loader = TableLoader(self.engine, batch_size=100)

    def test_required_column_missing(self):
        """Rows missing a required column should be skipped."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100"],
                ["2", "", "200"],  # name is empty/None
                ["3", "Charlie", "300"],
            ]
        )

        config = LoadConfig(
            table_name="req_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            required_columns=["id", "name"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE req_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2
        assert summary.rows_skipped == 1

    def test_no_required_columns_passes_all(self):
        """When no required columns are set, all rows pass."""
        tsv = _make_tsv(
            [
                ["1", "", ""],
                ["2", "Bob", "200"],
            ]
        )

        config = LoadConfig(
            table_name="all_pass_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE all_pass_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2


class TestSkipColumns:
    """Test skip columns functionality."""

    def setup_method(self):
        self.engine = _make_engine()
        self.loader = TableLoader(self.engine, batch_size=100)

    def test_skip_columns_excluded(self):
        """Specified skip columns should be excluded from the insert."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100"],
                ["2", "Bob", "200"],
            ]
        )

        config = LoadConfig(
            table_name="skip_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
            skip_columns=["amount"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE skip_table (id INTEGER PRIMARY KEY, name TEXT, amount REAL)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 2

        rows = _fetch(self.engine, "SELECT amount FROM skip_table WHERE id = 1")
        assert rows[0][0] is None


class TestCheckpoint:
    """Test checkpoint skip behavior."""

    def setup_method(self):
        self.engine = _make_engine()
        self.loader = TableLoader(self.engine, batch_size=100)

    def test_checkpoint_skips_same_file(self):
        """Loading the same file twice should skip on second load."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100"],
                ["2", "Bob", "200"],
            ]
        )

        config = LoadConfig(
            table_name="checkpoint_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE checkpoint_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        # First load
        summary1 = self.loader.load(config, tsv)
        assert summary1.rows_upserted == 2

        # Second load — same bytes, same hash → should skip
        summary2 = self.loader.load(config, tsv)
        assert summary2.rows_upserted == 0
        assert summary2.rows_skipped == 2

        # DB should still have exactly 2 rows (no duplicates)
        rows = _fetch(self.engine, "SELECT COUNT(*) FROM checkpoint_table")
        assert rows[0][0] == 2


class TestBatchUpsert:
    """Test batching behavior."""

    def setup_method(self):
        self.engine = _make_engine()
        self.loader = TableLoader(self.engine, batch_size=2)

    def test_batch_sends_multiple_batches(self):
        """5 rows with batch_size=2 should result in 3 batches (2+2+1)."""
        tsv = _make_tsv(
            [
                ["1", "Alice", "100"],
                ["2", "Bob", "200"],
                ["3", "Charlie", "300"],
                ["4", "Diana", "400"],
                ["5", "Eve", "500"],
            ]
        )

        config = LoadConfig(
            table_name="batch_table",
            tsv_files=["test.tsv"],
            conflict_columns=["id"],
        )
        _exec_sql(
            self.engine,
            "CREATE TABLE batch_table (id INTEGER PRIMARY KEY, name TEXT, amount TEXT)",
        )

        summary = self.loader.load(config, tsv)
        assert summary.rows_upserted == 5
        assert summary.rows_failed == 0


class TestGetLoadConfigs:
    """Test the get_load_configs helper."""

    def test_returns_empty_dict(self):
        """For now, get_load_configs should return an empty dict."""
        from core.etl.loader import get_load_configs

        result = get_load_configs()
        assert isinstance(result, dict)
        assert len(result) == 0
