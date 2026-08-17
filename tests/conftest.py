"""Shared test fixtures for the campaign finance DB test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_test_engine():
    """Create an in-memory SQLite engine with a minimal table set.

    Only creates the tables needed for integration tests:
    rcpt_cd, exppd_cd, filings, filername, ballot_measures, filing_calendar
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        # Filings table
        conn.execute(
            text("""
            CREATE TABLE filings (
                filing_id TEXT NOT NULL PRIMARY KEY,
                filing_type TEXT,
                filer_id TEXT,
                form_id TEXT,
                filing_date DATE,
                amend_id TEXT,
                election_date DATE,
                election_type TEXT,
                election_year INTEGER,
                period_id TEXT,
                session_id INTEGER,
                special_audit BOOLEAN DEFAULT FALSE,
                fine_audit BOOLEAN DEFAULT FALSE
            )
        """)
        )

        # RCPT_CD — receipts/contributions
        conn.execute(
            text("""
            CREATE TABLE rcpt_cd (
                filing_id TEXT NOT NULL,
                amend_id TEXT,
                line_item INTEGER,
                tran_id TEXT,
                ctrib_naml TEXT,
                ctrib_namf TEXT,
                amount REAL,
                receipt_dt DATE,
                payd_by TEXT,
                memo_refno TEXT,
                cmte_id TEXT,
                filer_id TEXT,
                election_date DATE,
                PRIMARY KEY (filing_id, amend_id, line_item)
            )
        """)
        )

        # EXPPD_CD — expenditures
        conn.execute(
            text("""
            CREATE TABLE exppd_cd (
                filing_id TEXT NOT NULL,
                amend_id TEXT,
                line_item INTEGER,
                tran_id TEXT,
                payee_naml TEXT,
                amount REAL,
                expn_date DATE,
                expn_dscr TEXT,
                memo_refno TEXT,
                filer_id TEXT,
                PRIMARY KEY (filing_id, amend_id, line_item)
            )
        """)
        )

        # FILERNAME — committee names
        conn.execute(
            text("""
            CREATE TABLE filername (
                xref_filer_id TEXT,
                filer_id TEXT NOT NULL,
                filer_type TEXT,
                status TEXT,
                effect_dt DATE,
                naml TEXT,
                namf TEXT,
                namt TEXT,
                nams TEXT,
                city TEXT,
                st TEXT,
                PRIMARY KEY (xref_filer_id, filer_id, effect_dt)
            )
        """)
        )

        # Ballot measures
        conn.execute(
            text("""
            CREATE TABLE ballot_measures (
                election_date DATE NOT NULL,
                filer_id TEXT NOT NULL,
                measure_no TEXT NOT NULL,
                measure_name TEXT NOT NULL,
                measure_short_name TEXT,
                jurisdiction TEXT NOT NULL,
                PRIMARY KEY (election_date, measure_no)
            )
        """)
        )

        # Filing calendar
        conn.execute(
            text("""
            CREATE TABLE filing_calendar (
                calendar_id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_date DATE NOT NULL,
                report_type TEXT NOT NULL,
                deadline_date DATE NOT NULL,
                grace_period_days INTEGER DEFAULT 0,
                source_url TEXT,
                notes TEXT
            )
        """)
        )

        # Load checkpoint (used by test_etl.py)
        conn.execute(
            text("""
            CREATE TABLE load_checkpoint (
                checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                loaded_at TIMESTAMP,
                UNIQUE(table_name, file_hash)
            )
        """)
        )

        # ETL dead letter (used by test_etl.py)
        conn.execute(
            text("""
            CREATE TABLE etl_dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                row_data TEXT NOT NULL,
                error_message TEXT NOT NULL,
                source_file TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        )

    return engine


def _seed_sample_data(engine):
    """Insert sample test data directly via SQL."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO filings VALUES "
                "('F001','QTR',NULL,NULL,NULL,NULL,'2024-11-05',NULL,2024,NULL,NULL,FALSE,FALSE)"
            )
        )

        # RCPT_CD — 3 contribution rows
        conn.execute(
            text(
                "INSERT INTO rcpt_cd VALUES "
                "('F001','A1',1,'T001','Alice Smith','',500.0, "
                "'2024-01-15',NULL,NULL,NULL,NULL,NULL),"
                "('F001','A1',2,'T002','Alice Smith','',250.0, "
                "'2024-02-10',NULL,NULL,NULL,NULL,NULL),"
                "('F001','A1',3,'T003','Bob Jones','',1000.0, "
                "'2024-03-01',NULL,NULL,NULL,NULL,NULL)"
            )
        )

        # EXPPD_CD — 2 expenditure rows
        conn.execute(
            text(
                "INSERT INTO exppd_cd VALUES "
                "('F001','A1',1,'E001','Acme Corp',300.0,'2024-01-20',NULL,NULL,NULL),"
                "('F001','A1',2,'E002','Bob Jones',750.0,'2024-02-15',NULL,NULL,NULL)"
            )
        )

        # FILERNAME — 2 committees
        conn.execute(
            text(
                "INSERT INTO filername VALUES "
                "(NULL,'C001',NULL,NULL,'2024-01-01','Test Committee',NULL,NULL,NULL,NULL,NULL),"
                "(NULL,'C002',NULL,NULL,'2024-01-01','Other Committee',NULL,NULL,NULL,NULL,NULL)"
            )
        )

        # Ballot measures
        conn.execute(
            text(
                "INSERT INTO ballot_measures VALUES "
                "('2024-11-05','C001','PROP 15','Property Tax Initiative','Prop 15','Statewide')"
            )
        )

        # Filing calendar
        conn.execute(
            text(
                "INSERT INTO filing_calendar VALUES "
                "(1,'2024-11-05','F496','2024-10-21',0,'https://www.sos.ca.gov','Quarterly report')"
            )
        )


@pytest.fixture()
def pg_engine():
    """SQLite engine with all test tables."""
    return _make_test_engine()


@pytest.fixture()
def loaded_db():
    """Engine with all sample data pre-loaded."""
    engine = _make_test_engine()
    _seed_sample_data(engine)
    return engine


@pytest.fixture()
def sample_rcpt_tsv():
    """Generate sample RCPT_CD TSV bytes with known data."""
    header = "filing_id\tamend_id\tline_item\ttran_id\tctrib_naml\tamount\treceipt_dt"
    rows = [
        "F001\tA1\t1\tT001\tAlice Smith\t500.00\t2024-01-15",
        "F001\tA1\t2\tT002\tAlice Smith\t250.00\t2024-02-10",
        "F001\tA1\t3\tT003\tBob Jones\t1000.00\t2024-03-01",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_exppd_tsv():
    """Generate sample EXPPD_CD TSV bytes with known data."""
    header = "filing_id\tamend_id\tline_item\ttran_id\tpayee_naml\tamount\texpn_date"
    rows = [
        "F001\tA1\t1\tE001\tAcme Corp\t300.00\t2024-01-20",
        "F001\tA1\t2\tE002\tBob Jones\t750.00\t2024-02-15",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_filername_tsv():
    """Generate sample FILERNAME_CD TSV bytes."""
    header = "filer_id\txref_filer_id\tnaml\tnamf\tnams\teffect_dt"
    rows = [
        "C001\t\tTest Committee\t\t\t2024-01-01",
        "C002\t\tOther Committee\t\t\t2024-01-01",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_filings_tsv():
    """Generate sample FILINGS_CD TSV bytes."""
    header = "filing_id\tfiler_id\trpt_type\telect_dt\telect_year"
    rows = [
        "F001\tC001\tQTR\t2024-11-05\t2024",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_ballot_tsv():
    """Generate sample BALLOT_MEASURES_CD TSV bytes."""
    header = "election_date\tfiler_id\tmeasure_no\tmeasure_name\tmeasure_short_name\tjurisdiction"
    rows = [
        "2024-11-05\tC001\tPROP 15\tProperty Tax Initiative\tProp 15\tStatewide",
    ]
    tsv = "\n".join([header] + rows).encode("utf-8")
    return tsv


@pytest.fixture()
def sample_filing_calendar_tsv():
    """Generate sample FILING_CALENDAR TSV bytes."""
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
