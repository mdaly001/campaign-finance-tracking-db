"""Tests for Step 2: SourceAdapter interface + ETL utilities."""

import csv
import io


class TestImportAllModules:
    """Verify every module imports without error."""

    def test_import_adapter(self):
        from core.etl import adapter  # noqa: F401

    def test_import_tsv(self):
        from core.etl import tsv  # noqa: F401

    def test_import_checkpoint(self):
        from core.etl import checkpoint  # noqa: F401

    def test_import_upsert(self):
        from core.etl import upsert  # noqa: F401

    def test_import_dead_letter(self):
        from core.etl import dead_letter  # noqa: F401

    def test_import_logging(self):
        from core.etl import logging  # noqa: F401

    def test_import_init_exposes_all(self):
        from core.etl import (
            DeadLetter,
            LoadCheckpoint,
            LoadSummary,
            SourceAdapter,
            SourceFileInfo,
            TSVReader,
            setup_logging,
            upsert_records,
        )

        # All should be importable
        assert SourceAdapter is not None
        assert SourceFileInfo is not None
        assert LoadSummary is not None
        assert TSVReader is not None
        assert LoadCheckpoint is not None
        assert DeadLetter is not None
        assert upsert_records is not None
        assert setup_logging is not None


class TestSourceAdapterAbstractMethods:
    """Verify SourceAdapter has all 5 required abstract methods."""

    def test_abstract_methods_exist(self):
        from core.etl.adapter import SourceAdapter

        abstract_methods = [
            name
            for name, method in SourceAdapter.__dict__.items()
            if hasattr(method, "__isabstractmethod__") and method.__isabstractmethod__
        ]

        expected = {
            "get_source_files",
            "fetch_file",
            "parse_file",
            "upsert_records",
            "compute_checksum",
        }
        actual = set(abstract_methods)
        assert actual == expected, f"SourceAdapter abstract methods: {actual}\nExpected: {expected}"


class TestSourceFileInfoDataclass:
    """Test SourceFileInfo dataclass."""

    def test_defaults(self):
        from core.etl.adapter import SourceFileInfo

        info = SourceFileInfo(name="test.tsv", url="http://example.com/test.tsv")
        assert info.name == "test.tsv"
        assert info.url == "http://example.com/test.tsv"
        assert info.checksum is None
        assert info.size == 0

    def test_with_checksum(self):
        from core.etl.adapter import SourceFileInfo

        info = SourceFileInfo(
            name="test.tsv",
            url="http://example.com/test.tsv",
            checksum="abc123",
        )
        assert info.checksum == "abc123"


class TestLoadSummaryDataclass:
    """Test LoadSummary dataclass."""

    def test_defaults(self):
        from core.etl.adapter import LoadSummary

        summary = LoadSummary()
        assert summary.rows_read == 0
        assert summary.rows_upserted == 0
        assert summary.rows_skipped == 0
        assert summary.rows_failed == 0
        assert summary.duration_seconds == 0.0

    def test_custom_values(self):
        from core.etl.adapter import LoadSummary

        summary = LoadSummary(rows_read=100, rows_upserted=95, rows_failed=5)
        assert summary.rows_read == 100
        assert summary.rows_upserted == 95
        assert summary.rows_failed == 5


class TestTSVReader:
    """Test TSVReader parsing."""

    def _make_tsv(self, header: str, rows: list[str]) -> str:
        """Build a TSV string from header and data rows."""
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t")
        writer.writerow(header.split("\t"))
        for row in rows:
            writer.writerow(row.split("\t"))
        return buf.getvalue()

    def test_parse_basic(self):
        from core.etl.tsv import TSVReader

        tsv_str = self._make_tsv("id\tname\tvalue", ["1\tAlice\t100", "2\tBob\t200"])
        reader = TSVReader()
        result = reader.read_string(tsv_str)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[0]["name"] == "Alice"
        assert result[0]["value"] == "100"
        assert result[1]["name"] == "Bob"

    def test_nul_bytes_stripped(self):
        """Regression: the real SOS export contains NUL (0x00) bytes in
        free-text fields; Postgres rejects them in every string type, so
        the reader must strip them at parse time."""
        from core.etl.tsv import TSVReader

        tsv_str = "id\tname\n1\tAli\x00ce\n2\tBob\n"
        result = TSVReader().read_string(tsv_str)
        assert result[0]["name"] == "Alice"
        assert result[1]["name"] == "Bob"

    def test_embedded_control_chars_do_not_split_rows(self):
        """Regression: the real SOS export is CRLF-terminated but free-text
        fields contain embedded control chars (bare \\r, \\x0b, \\x1c-\\x1e)
        that str.splitlines() would treat as line boundaries, creating a
        truncated row plus a garbage fragment row. Parsing must split on
        LF only, after normalizing CRLF."""
        from core.etl.tsv import TSVReader

        tsv = "id\tname\tmemo\r\n1\tAlice\thas\x0bVT inside\r\n2\tBob\ttext with\rbare CR\r\n"
        result = TSVReader().read_string(tsv)
        assert len(result) == 2
        assert result[0]["memo"] == "has\x0bVT inside"
        assert result[1]["memo"] == "text with\rbare CR"

    def test_ragged_wide_row_extras_merged_into_last_column(self):
        """Regression: rows with MORE fields than the header (unescaped
        tabs in free text) must not crash; extras are merged into the last
        column preserving the data. Short rows get None for missing cols."""
        from core.etl.tsv import TSVReader

        tsv_str = "id\tname\tvalue\n1\tO'Brien\t100\tEXTRA\n2\tBob\n"
        result = TSVReader().read_string(tsv_str)
        assert result[0]["value"] == "100\tEXTRA"
        assert result[1]["name"] == "Bob"
        assert result[1]["value"] is None

    def test_empty_to_none(self):
        from core.etl.tsv import TSVReader

        tsv_str = self._make_tsv("id\tname\tvalue", ["1\tAlice\t", "2\t\t200"])
        reader = TSVReader(empty_to_none=True)
        result = reader.read_string(tsv_str)
        assert result[0]["value"] is None
        assert result[1]["name"] is None

    def test_no_empty_to_none(self):
        from core.etl.tsv import TSVReader

        tsv_str = self._make_tsv("id\tname\tvalue", ["1\tAlice\t", "2\t\t200"])
        reader = TSVReader(empty_to_none=False)
        result = reader.read_string(tsv_str)
        assert result[0]["value"] == ""
        assert result[1]["name"] == ""

    def test_coercion_hints(self):
        from core.etl.tsv import TSVReader

        tsv_str = self._make_tsv("id\tname\tamount", ["1\tAlice\t100", "2\tBob\t200.5"])
        reader = TSVReader(coercion_hints={"id": int, "amount": float})
        result = reader.read_string(tsv_str)
        assert result[0]["id"] == 1
        assert isinstance(result[0]["id"], int)
        assert result[0]["amount"] == 100.0
        assert isinstance(result[0]["amount"], float)
        assert result[1]["amount"] == 200.5

    def test_read_file(self, tmp_path):
        from core.etl.tsv import TSVReader

        tsv_path = tmp_path / "sample.tsv"
        tsv_path.write_text("id\tname\n1\tAlice\n2\tBob\n", encoding="utf-8")

        reader = TSVReader()
        result = reader.read_file(str(tsv_path))
        assert len(result) == 2
        assert result[0]["name"] == "Alice"

    def test_read_bytes(self):
        from core.etl.tsv import TSVReader

        raw = b"id\tname\n1\tAlice\n2\tBob\n"
        reader = TSVReader()
        result = reader.read_bytes(raw)
        assert len(result) == 2

    def test_no_header(self):
        from core.etl.tsv import TSVReader

        tsv_str = "1\tAlice\n2\tBob\n"
        reader = TSVReader(has_header=False, coercion_hints={"f0": int})
        result = reader.read_string(tsv_str)
        assert len(result) == 2
        assert result[0]["f0"] == 1
        assert result[0]["f1"] == "Alice"
        assert result[1]["f0"] == 2
        assert result[1]["f1"] == "Bob"

    def test_empty_tsv(self):
        from core.etl.tsv import TSVReader

        reader = TSVReader()
        result = reader.read_string("")
        assert result == []

    def test_header_only(self):
        from core.etl.tsv import TSVReader

        reader = TSVReader()
        result = reader.read_string("id\tname\tvalue\n")
        assert result == []


class TestUpsterFunctionSignature:
    """Test that upsert_records has the expected signature."""

    def test_exists_and_callable(self):
        from core.etl.upsert import upsert_records

        assert callable(upsert_records)

    def test_signature(self):
        import inspect

        from core.etl.upsert import upsert_records

        sig = inspect.signature(upsert_records)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "table_name" in params
        assert "records" in params
        assert "conflict_columns" in params
        assert "batch_size" in params

        # batch_size default should be 1000
        assert sig.parameters["batch_size"].default == 1000


class TestLoadCheckpointClass:
    """Verify LoadCheckpoint has expected methods."""

    def test_methods_exist(self):
        from core.etl.checkpoint import LoadCheckpoint

        assert hasattr(LoadCheckpoint, "__init__")
        assert hasattr(LoadCheckpoint, "load_checkpoint")
        assert hasattr(LoadCheckpoint, "get_checkpoint")
        assert hasattr(LoadCheckpoint, "get_unchecked_tables")


class TestDeadLetterClass:
    """Verify DeadLetter has expected methods."""

    def test_methods_exist(self):
        from core.etl.dead_letter import DeadLetter

        assert hasattr(DeadLetter, "__init__")
        assert hasattr(DeadLetter, "quarantine")
        assert hasattr(DeadLetter, "get_dead_letters")

    def test_quarantine_commits_and_handles_decimal(self, tmp_path):
        """Regression: quarantine must COMMIT (a bare connect() rolls back
        on close, silently dropping the audit trail) and must serialize
        Decimal/datetime values (coerced rows are not JSON-native)."""
        import json
        from decimal import Decimal

        from sqlalchemy import create_engine, text

        from core.etl.dead_letter import DeadLetter

        db = tmp_path / "dl_test.db"
        engine = create_engine(f"sqlite:///{db}")
        with engine.begin() as conn:
            conn.execute(
                text(
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
            )

        dead = DeadLetter(engine)
        dead.quarantine(
            "rcpt_cd",
            {"amount": Decimal("500.00"), "name": "X", "dt": None},
            "test failure",
            "RCPT_CD.TSV",
        )

        # Read back on a FRESH connection: proves the insert was committed.
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT table_name, row_data, error_message FROM etl_dead_letter")
            ).fetchone()
        assert row is not None, "dead-letter row was rolled back (missing commit)"
        assert row[0] == "rcpt_cd"
        data = json.loads(row[1])
        assert data["amount"] == "500.00"  # Decimal -> str via default=str
        assert row[2] == "test failure"


class TestSetupLogging:
    """Test that setup_logging can be called without error."""

    def test_setup_logging_no_error(self):
        from core.etl.logging import setup_logging

        setup_logging(level="DEBUG")  # should not raise

    def test_setup_logging_with_file(self, tmp_path):
        from core.etl.logging import setup_logging

        log_file = str(tmp_path / "test.log")
        # Should not raise even if file doesn't exist yet
        setup_logging(level="INFO", log_file=log_file)
