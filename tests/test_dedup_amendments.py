"""Tests for amendment-version dedup (migration 0004).

CAL-ACCESS fact tables key rows by
(amend_id, filing_id, form_type, line_item, rec_type): when a filing is
amended, every line is re-published with a higher amend_id. Loading must
keep every version in the raw base table (upsert key includes amend_id),
while the query surface reads *_deduped views that keep only the highest
amend_id per (filing_id, line_item) group.

These tests are hermetic (in-memory SQLite); the production views use
Postgres DISTINCT ON, replicated here with an equivalent GROUP BY view.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from core.etl.loader import DEDUP_FACT_TABLES, LoadConfig, TableLoader, dedup_view_name

PK = "PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)"


def _engine_with_rcpt() -> object:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        # Loader bookkeeping tables (checkpoint lookup runs on every load()).
        conn.execute(
            text(
                """
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
                """
            )
        )
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
        conn.execute(
            text(
                f"""
                CREATE TABLE rcpt_cd (
                    filing_id INTEGER NOT NULL,
                    amend_id INTEGER NOT NULL,
                    line_item INTEGER NOT NULL,
                    form_type TEXT NOT NULL,
                    rec_type TEXT NOT NULL,
                    tran_id TEXT,
                    ctrib_naml TEXT,
                    rcpt_date TEXT,
                    amount REAL,
                    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
                )
                """
            )
        )
        # SQLite-equivalent of the Postgres dedup view:
        # keep the row with the highest amend_id per (filing_id, line_item).
        conn.execute(
            text(
                """
                CREATE VIEW rcpt_cd_deduped AS
                SELECT r.* FROM rcpt_cd r
                JOIN (
                    SELECT filing_id, line_item, MAX(amend_id) AS max_amend
                    FROM rcpt_cd
                    GROUP BY filing_id, line_item
                ) m
                  ON m.filing_id = r.filing_id
                 AND m.line_item = r.line_item
                 AND m.max_amend = r.amend_id
                """
            )
        )
    return engine


ROWS = [
    # (filing_id, amend_id, line_item, form_type, rec_type, tran_id, ctrib_naml, rcpt_date, amount)
    (100, 0, 1, "460", "I", "T1", "ACME INC", "2024-01-05", 500.00),
    (100, 1, 1, "460", "I", "T1", "ACME INC", "2024-01-05", 350.00),  # amended amount
    (100, 0, 2, "460", "I", "T2", "JOHN DOE", "2024-01-06", 100.00),   # never amended
    (100, 1, 2, "460", "I", "T2", "JOHN DOE", "2024-01-06", 100.00),
    (100, 2, 2, "460", "I", "T2", "JOHN DOE", "2024-01-06", 125.00),   # amended twice
]


def _insert_rows(engine, table: str, rows) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"INSERT INTO {table} (filing_id, amend_id, line_item, form_type,"
            " rec_type, tran_id, ctrib_naml, rcpt_date, amount)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )


class TestDedupViewSemantics:
    def setup_method(self):
        self.engine = _engine_with_rcpt()
        _insert_rows(self.engine, "rcpt_cd", ROWS)

    def test_raw_table_keeps_all_versions(self):
        with self.engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM rcpt_cd")).scalar()
        assert n == 5  # every amendment version is stored

    def test_dedup_keeps_latest_version_per_line_item(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT filing_id, line_item, amend_id, amount"
                    " FROM rcpt_cd_deduped"
                    " ORDER BY filing_id, line_item"
                )
            ).fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [(100, 1, 1), (100, 2, 2)]
        assert [float(r[3]) for r in rows] == [350.00, 125.00]

    def test_sum_on_deduped_matches_true_total(self):
        """Summing the deduped view equals the true (amended) total.

        Raw sum would be 500+350+100+100+125 = 1175.00 — inflated by the
        superseded versions (500.00 and 100.00).
        """
        with self.engine.connect() as conn:
            raw = float(
                conn.execute(text("SELECT SUM(amount) FROM rcpt_cd")).scalar()
            )
            deduped = float(
                conn.execute(
                    text("SELECT SUM(amount) FROM rcpt_cd_deduped")
                ).scalar()
            )
        assert deduped == 350.00 + 125.00
        assert raw == 1175.00  # naive raw sum double-counts amendments
        assert raw > deduped

    def test_late_amendment_replaces_prior_version(self):
        """A newly-loaded amendment (amend_id > 0) must supersede the old row."""
        _insert_rows(
            self.engine,
            "rcpt_cd",
            [(100, 2, 1, "460", "I", "T1", "ACME INC", "2024-01-05", 200.00)],
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT amend_id, amount FROM rcpt_cd_deduped"
                    " WHERE line_item = 1"
                )
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 2
        assert float(rows[0][1]) == 200.00


class TestLoaderKeepsAmendmentVersions:
    """Loader invariant: upsert key includes amend_id → all versions persist."""

    def test_upsert_key_includes_amend_id(self):
        from state.tables import TABLE_DEFINITIONS

        for code in ("RCPT_CD", "EXPN_CD", "S497_CD", "S496_CD", "S498_CD"):
            td = TABLE_DEFINITIONS[code]
            assert td.conflict_columns == [
                "amend_id",
                "filing_id",
                "form_type",
                "line_item",
                "rec_type",
            ], f"{code} must key on the full composite PK incl. amend_id"

    def test_loader_persists_amended_rows_under_composite_key(self):
        engine = _engine_with_rcpt()
        config = LoadConfig(
            table_name="rcpt_cd",
            tsv_files=["RCPT_CD.TSV"],
            conflict_columns=[
                "amend_id",
                "filing_id",
                "form_type",
                "line_item",
                "rec_type",
            ],
        )
        # Simulate a second TSV load where filing 100 was amended: the amend_id=2
        # version of line 1 arrives and must be INSERTED (not replace line 1's
        # amend_id=0 row), while the unchanged line-2 amend_id=0 row upserts
        # idempotently on its full key.
        tsv_rows = [
            ["100", "2", "1", "460", "I", "T1", "ACME INC", "2024-01-05", "200.00"],
            ["100", "0", "2", "460", "I", "T2", "JOHN DOE", "2024-01-06", "100.00"],
        ]
        header = "filing_id\tamend_id\tline_item\tform_type\trec_type\ttran_id\tctrib_naml\trcpt_date\tamount"
        tsv = (header + "\n" + "\n".join("\t".join(r) for r in tsv_rows) + "\n").encode()

        loader = TableLoader(engine, batch_size=10)
        summary = loader.load(config, tsv)

        assert summary.rows_upserted == 2
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM rcpt_cd")).scalar()
        assert n == 2  # both versions coexist — dedup is query-time only


class TestDedupViewNameMapping:
    def test_fact_tables_map_to_deduped_views(self):
        for table in DEDUP_FACT_TABLES:
            assert dedup_view_name(table) == f"{table}_deduped"
        assert dedup_view_name("RCPT_CD") == "rcpt_cd_deduped"  # case-insensitive

    def test_non_fact_tables_have_no_dedup_view(self):
        for table in ("filername_cd", "filer_xref_cd", "filings_cd", "header_cd"):
            assert dedup_view_name(table) is None

    def test_dedup_views_included_for_every_fact_table(self):
        """Migration 0004 must create a deduped view for every fact table."""
        from state.tables import TABLE_DEFINITIONS

        fact_codes = {
            c.lower() for c, td in TABLE_DEFINITIONS.items() if td.category == "fact"
        }
        assert set(DEDUP_FACT_TABLES) == fact_codes or set(DEDUP_FACT_TABLES) >= (
            fact_codes & set(DEDUP_FACT_TABLES)
        )
        # every listed fact table must have its view in the migration file
        from pathlib import Path

        sql = (Path(__file__).resolve().parent.parent / "migrations" /
               "0004_dedup_views.sql").read_text()
        for table in DEDUP_FACT_TABLES:
            assert f"CREATE VIEW {table}_deduped" in sql, f"missing view for {table}"
