"""Shared test fixtures for the campaign finance DB test suite.

The in-memory SQLite schema mirrors the REAL CAL-ACCESS export layout
(2002 data model): 5-column composite PKs on detail tables, surrogate
PK on filername_cd, cmte_id -> filer_xref_cd -> filername_cd name
resolution, and no election_year column on filings (cycle is derived
from transaction dates).
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

# The loader coerces numeric TSV fields to Decimal (Postgres NUMERIC).
# SQLite's DBAPI cannot bind Decimal, so adapt it to float for tests.
sqlite3.register_adapter(Decimal, float)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_test_engine():
    """Create an in-memory SQLite engine with the real-schema table set.

    Tables: rcpt_cd, expn_cd, smry_cd, filings_cd, filername_cd,
    filer_xref_cd, ballot_measures_cd, filing_calendar, load_checkpoint,
    etl_dead_letter.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        # filings_cd — real schema has only filing_id + filing_type
        conn.execute(
            text("""
            CREATE TABLE filings_cd (
                filing_id INTEGER PRIMARY KEY,
                filing_type INTEGER
            )
        """)
        )

        # rcpt_cd — contributions; 5-col composite PK
        conn.execute(
            text("""
            CREATE TABLE rcpt_cd (
                filing_id INTEGER NOT NULL,
                amend_id INTEGER NOT NULL,
                line_item INTEGER NOT NULL,
                form_type TEXT NOT NULL,
                rec_type TEXT NOT NULL,
                tran_id TEXT,
                ctrib_naml TEXT,
                ctrib_namf TEXT,
                ctrib_dscr TEXT,
                rcpt_date TIMESTAMP,
                amount NUMERIC,
                cmte_id TEXT,
                memo_refno TEXT,
                PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
            )
        """)
        )

        # expn_cd — expenditures; 5-col composite PK
        conn.execute(
            text("""
            CREATE TABLE expn_cd (
                filing_id INTEGER NOT NULL,
                amend_id INTEGER NOT NULL,
                line_item INTEGER NOT NULL,
                form_type TEXT NOT NULL,
                rec_type TEXT NOT NULL,
                tran_id TEXT,
                payee_naml TEXT,
                payee_namf TEXT,
                expn_dscr TEXT,
                expn_date TIMESTAMP,
                amount NUMERIC,
                cmte_id TEXT,
                memo_refno TEXT,
                PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
            )
        """)
        )

        # smry_cd — summary lines; line_item is TEXT in the real export
        conn.execute(
            text("""
            CREATE TABLE smry_cd (
                filing_id INTEGER NOT NULL,
                amend_id INTEGER NOT NULL,
                line_item TEXT NOT NULL,
                form_type TEXT NOT NULL,
                rec_type TEXT NOT NULL,
                amount_a NUMERIC,
                amount_b NUMERIC,
                amount_c NUMERIC,
                elec_dt TIMESTAMP,
                PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
            )
        """)
        )

        # filername_cd — committee/committee names; surrogate PK
        conn.execute(
            text("""
            CREATE TABLE filername_cd (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filer_id INTEGER,
                filer_type TEXT,
                status TEXT,
                effect_dt TIMESTAMP,
                naml TEXT,
                namf TEXT,
                namt TEXT,
                nams TEXT,
                city TEXT,
                st TEXT
            )
        """)
        )

        # filer_xref_cd — cmte_id (xref_id) -> filer_id mapping
        conn.execute(
            text("""
            CREATE TABLE filer_xref_cd (
                filer_id INTEGER,
                xref_id TEXT,
                effect_dt TIMESTAMP,
                migration_source TEXT
            )
        """)
        )

        # ballot_measures_cd
        conn.execute(
            text("""
            CREATE TABLE ballot_measures_cd (
                election_date TIMESTAMP,
                filer_id INTEGER,
                measure_no TEXT,
                measure_name TEXT,
                measure_short_name TEXT,
                jurisdiction TEXT
            )
        """)
        )

        # filing_calendar — scraper-owned
        conn.execute(
            text("""
            CREATE TABLE filing_calendar (
                calendar_id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_date DATE,
                report_type TEXT,
                deadline_date DATE,
                grace_period_days INTEGER,
                source_url TEXT,
                notes TEXT
            )
        """)
        )

        # load_checkpoint
        conn.execute(
            text("""
            CREATE TABLE load_checkpoint (
                checkpoint_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name     TEXT NOT NULL,
                source         TEXT NOT NULL DEFAULT 'calaccess',
                file_hash      TEXT NOT NULL,
                source_file    TEXT,
                processed_date TIMESTAMP,
                rows_processed INTEGER,
                notes          TEXT,
                UNIQUE(table_name, source, file_hash)
            )
        """)
        )

        # etl_dead_letter
        conn.execute(
            text("""
            CREATE TABLE etl_dead_letter (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name      TEXT NOT NULL,
                row_data        TEXT NOT NULL,
                error_message   TEXT NOT NULL,
                source_file     TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        )

    _seed_sample_data(engine)
    return engine


def _seed_sample_data(engine):
    """Insert deterministic sample rows for integration tests.

    Numbers:
      Contributions to C001 (2024): Alice 500 + 250, Bob 1000 → 1750
      Expenditures from C001 (2024): Acme 300, Bob 750 → 1050
      Net cash: +700
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO filings_cd (filing_id, filing_type) VALUES "
                "(100, 1), (101, 1), (102, 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO rcpt_cd (filing_id, amend_id, line_item, form_type, "
                "rec_type, tran_id, ctrib_naml, ctrib_namf, amount, rcpt_date, cmte_id) "
                "VALUES "
                "(100, 0, 1, 'F460', 'I', 'T1', 'Smith', 'Alice', 500.0, '2024-05-15', 'C001'), "
                "(101, 0, 1, 'F460', 'I', 'T2', 'Smith', 'Alice', 250.0, '2024-06-01', 'C001'), "
                "(102, 0, 1, 'F460', 'I', 'T3', 'Jones', 'Bob', 1000.0, '2024-07-01', 'C001')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO expn_cd (filing_id, amend_id, line_item, form_type, "
                "rec_type, tran_id, payee_naml, payee_namf, amount, expn_date, cmte_id) "
                "VALUES "
                "(100, 0, 1, 'F460', 'E', 'E1', 'Acme', 'Corp', 300.0, '2024-05-20', 'C001'), "
                "(101, 0, 1, 'F460', 'E', 'E2', 'Jones', 'Bob', 750.0, '2024-06-10', 'C001')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO filername_cd (filer_id, filer_type, status, effect_dt, "
                "naml, namf, city, st) VALUES "
                "(1001, 'PC', 'Active', '2023-01-01', 'Test', 'Committee', 'Sacramento', 'CA')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO filer_xref_cd (filer_id, xref_id, effect_dt) VALUES "
                "(1001, 'C001', '2023-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO ballot_measures_cd (election_date, filer_id, measure_no, "
                "measure_name, measure_short_name, jurisdiction) VALUES "
                "('2024-11-05', 2001, '15', 'Property Tax Initiative', 'Prop 15', 'Statewide')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO filing_calendar (election_date, report_type, deadline_date, "
                "grace_period_days, source_url, notes) VALUES "
                "('2024-11-05', 'F496', '2024-10-21', 15, 'https://www.sos.ca.gov', 'Quarterly report')"
            )
        )


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the project root directory."""
    return PROJECT_ROOT


@pytest.fixture
def pg_engine():
    """In-memory SQLite engine mirroring the real CAL-ACCESS schema."""
    return _make_test_engine()


@pytest.fixture
def loaded_db(pg_engine):
    """Engine with sample data for integration queries."""
    return pg_engine


# ------------------------------------------------------------------ #
#  TSV fixtures (real export column headers, M/D/YYYY dates)
# ------------------------------------------------------------------ #


@pytest.fixture()
def sample_rcpt_tsv():
    """Generate sample RCPT_CD TSV bytes (real headers)."""
    header = "\t".join(
        [
            "filing_id",
            "amend_id",
            "line_item",
            "form_type",
            "rec_type",
            "tran_id",
            "ctrib_naml",
            "ctrib_namf",
            "amount",
            "rcpt_date",
            "cmte_id",
        ]
    )
    rows = [
        "1\t0\t1\tF460\tI\tT1\tSmith\tAlice\t100.00\t5/15/2024\tC001",
        "2\t0\t1\tF460\tI\tT2\tJones\tBob\t200.50\t6/20/2024\tC002",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_expn_tsv():
    """Generate sample EXPN_CD TSV bytes (real headers)."""
    header = "\t".join(
        [
            "filing_id",
            "amend_id",
            "line_item",
            "form_type",
            "rec_type",
            "tran_id",
            "payee_naml",
            "payee_namf",
            "amount",
            "expn_date",
            "cmte_id",
        ]
    )
    rows = [
        "1\t0\t1\tF460\tE\tE1\tAcme\tCorp\t300.00\t5/20/2024\tC001",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_filername_tsv():
    """Generate sample FILERNAME_CD TSV bytes (surrogate table)."""
    header = "\t".join(["filer_id", "filer_type", "status", "effect_dt", "naml", "namf"])
    rows = [
        "1001\tPC\tActive\t1/1/2023\tTest\tCommittee",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_filings_tsv():
    """Generate sample FILINGS_CD TSV bytes (real: filing_id + filing_type)."""
    header = "filing_id\tfiling_type"
    rows = [
        "100\t1",
        "101\t2",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_ballot_tsv():
    """Generate sample BALLOT_MEASURES_CD TSV bytes."""
    header = "\t".join(
        ["election_date", "filer_id", "measure_no", "measure_name",
         "measure_short_name", "jurisdiction"]
    )
    rows = [
        "11/5/2024\t2001\t15\tProperty Tax Initiative\tProp 15\tStatewide",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_filing_calendar_tsv():
    """Generate sample FILING_CALENDAR TSV bytes (scraper-owned)."""
    hdr = "calendar_id\telection_date\treport_type"
    hdr += "\tdeadline_date\tgrace_period_days"
    hdr += "\tsource_url\tnotes"
    rows = [
        "1\t2024-11-05\tF496\t2024-10-21\t0\thttps://www.sos.ca.gov\tQuarterly report",
    ]
    tsv = "\n".join([hdr] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def checksum_cache_dir(tmp_path):
    """Temporary directory for state adapter cache tests."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir
