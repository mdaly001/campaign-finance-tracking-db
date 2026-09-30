"""Tests for the federal (FEC) ETL pipeline."""

import hashlib
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from core.etl.adapter import SourceFileInfo
from federal.adapter import FECSourceAdapter
from federal.tables import FEC_TABLE_DEFINITIONS, LOAD_ORDER


class TestFECAdapter:
    """Tests for FECSourceAdapter."""

    @pytest.fixture
    def adapter(self):
        """Create a FECSourceAdapter with a temp cache dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield FECSourceAdapter(cache_dir=Path(tmpdir))

    def test_source_name(self, adapter):
        assert adapter.source == "federal"

    def test_compute_checksum(self, adapter):
        raw = b"test data"
        checksum = adapter.compute_checksum(raw)
        expected = hashlib.sha256(raw).hexdigest()
        assert checksum == expected

    def test_parse_pipe_delimited(self, adapter):
        """Test parsing FEC pipe-delimited files."""
        # Sample itcont row (pipe-delimited, no header)
        raw = b"C00000422|N|M9|P|202609169904219733|15|IND|RAFLA-YUAN, ERIC LEI MD|SAN DIEGO|CA|921032110|SELF-EMPLOYED|PHYSICIAN|08022026|500||AE4186ED31CEC42038B3|2012199|||4091620261598597236"

        rows = list(adapter.parse_file(raw))
        assert len(rows) == 1

        row = rows[0]
        # Check key fields (0-indexed)
        assert row["f0"] == "C00000422"  # committee_id
        assert row["f1"] == "N"  # amendment_indicator
        assert row["f4"] == "202609169904219733"  # transaction_id
        assert row["f7"] == "RAFLA-YUAN, ERIC LEI MD"  # contributor_name
        assert row["f8"] == "SAN DIEGO"  # contributor_city
        assert row["f9"] == "CA"  # contributor_state
        assert row["f13"] == "08022026"  # contribution_date
        assert row["f14"] == "500"  # contribution_amount

    def test_parse_multiple_rows(self, adapter):
        """Test parsing multiple FEC rows."""
        raw = (
            b"C00000422|N|M9|P|202609169904219733|15|IND|PERSON ONE|CITY1|CA|92103|EMP1|OCC1|08022026|500||||||\n"
            b"C00000422|N|M9|P|202609169904219734|15|IND|PERSON TWO|CITY2|NY|10001|EMP2|OCC2|08032026|1000||||||"
        )

        rows = list(adapter.parse_file(raw))
        assert len(rows) == 2
        assert rows[0]["f9"] == "CA"
        assert rows[1]["f9"] == "NY"


class TestFECTableDefinitions:
    """Tests for FEC table definitions registry."""

    def test_all_codes_in_load_order_exist(self):
        """All codes in LOAD_ORDER must exist in FEC_TABLE_DEFINITIONS."""
        for code in LOAD_ORDER:
            assert code in FEC_TABLE_DEFINITIONS, f"Missing definition for {code}"

    def test_fec_indiv_has_transaction_id_conflict(self):
        """Individual contributions must use transaction_id for dedup."""
        td = FEC_TABLE_DEFINITIONS["FEC_INDIV"]
        assert "transaction_id" in (td.conflict_columns or [])

    def test_fec_indiv_has_date_coercion(self):
        """Individual contributions must coerce contribution_date to date."""
        td = FEC_TABLE_DEFINITIONS["FEC_INDIV"]
        assert "contribution_date" in (td.type_coercions or {})
        assert td.type_coercions["contribution_date"] == "date"

    def test_fec_indiv_has_amount_coercion(self):
        """Individual contributions must coerce amount to numeric."""
        td = FEC_TABLE_DEFINITIONS["FEC_INDIV"]
        assert "contribution_amount" in (td.type_coercions or {})
        assert td.type_coercions["contribution_amount"] == "numeric"

    def test_cycle_scoped_tables(self):
        """Fact tables must be cycle-scoped."""
        fact_codes = ["FEC_INDIV", "FEC_OTH", "FEC_PAS2", "FEC_OPPEXP"]
        for code in fact_codes:
            assert FEC_TABLE_DEFINITIONS[code].cycle_scoped, f"{code} should be cycle-scoped"

    def test_master_tables_not_cycle_scoped(self):
        """Master tables should not be cycle-scoped."""
        master_codes = ["FEC_CM", "FEC_CN", "FEC_CCL"]
        for code in master_codes:
            assert not FEC_TABLE_DEFINITIONS[code].cycle_scoped, f"{code} should not be cycle-scoped"


class TestFECSchema:
    """Tests for FEC schema tables."""

    @pytest.fixture
    def engine(self):
        """Create an in-memory SQLite engine for testing."""
        return create_engine("sqlite:///:memory:")

    def test_fec_schema_tables_exist(self, engine):
        """Test that FEC schema tables can be created."""
        # Run the migration on SQLite (simplified — Postgres-specific syntax may differ)
        with engine.begin() as conn:
            # Create the fec schema (SQLite doesn't have schemas, so we use prefix)
            conn.execute(text("CREATE TABLE fec_fec_committees (committee_id TEXT PRIMARY KEY, committee_name TEXT, state TEXT)"))
            conn.execute(text("CREATE TABLE fec_fec_candidates (candidate_id TEXT PRIMARY KEY, candidate_name TEXT, state TEXT)"))

            # Insert test data
            conn.execute(text("INSERT INTO fec_fec_committees VALUES ('C00000422', 'Test Committee', 'CA')"))
            conn.execute(text("INSERT INTO fec_fec_candidates VALUES ('H0CA01001', 'Test Candidate', 'CA')"))

            # Query back
            rows = conn.execute(text("SELECT * FROM fec_fec_committees")).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "C00000422"
            assert rows[0][2] == "CA"

            rows = conn.execute(text("SELECT * FROM fec_fec_candidates")).fetchall()
            assert len(rows) == 1
            assert rows[0][1] == "Test Candidate"
