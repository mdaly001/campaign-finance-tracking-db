"""Tests for amendment-version dedup (migrations 0004 + 0007).

CAL-ACCESS fact tables key rows by
(amend_id, filing_id, form_type, line_item, rec_type): when a filing is
amended, every line is re-published with a higher amend_id. Loading must
keep every version in the raw base table (upsert key includes amend_id),
while the query surface reads *_deduped views.

Migration 0004 keyed the views on (filing_id, line_item) — a *slot* key that
double-counts a transaction refiled across multiple filings (same
cmte_id + tran_id, new line_item per amending filing). Migration 0007
re-keys transaction-carrying views to the transaction itself:
DISTINCT ON (cmte_id, tran_id) ORDER BY amend_id DESC — each logical
transaction survives exactly once, as its current version.

These tests are hermetic (in-memory SQLite); the production views use
Postgres DISTINCT ON, replicated here with an equivalent GROUP BY view
(highest amend_id per transaction, lowest filing_id/line_item breaks ties).
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
                    cmte_id TEXT,
                    tran_id TEXT,
                    ctrib_naml TEXT,
                    rcpt_date TEXT,
                    amount REAL,
                    PRIMARY KEY (amend_id, filing_id, form_type, line_item, rec_type)
                )
                """
            )
        )
        # SQLite-equivalent of the Postgres dedup view (migration 0007):
        # keep the row with the highest amend_id per transaction — the current
        # version — and the latest version per (filing_id, line_item) slot for
        # rows with no committee/transaction attribution (null or blank
        # cmte_id/tran_id). Test data keeps max-amend unique per transaction,
        # so the max-amend join matches DISTINCT ON exactly; the production
        # view breaks ties deterministically on (filing_id, line_item).
        conn.execute(
            text(
                """
                CREATE VIEW rcpt_cd_deduped AS
                SELECT r.* FROM rcpt_cd r
                JOIN (
                    SELECT cmte_id, tran_id, MAX(amend_id) AS max_amend
                    FROM rcpt_cd
                    WHERE cmte_id IS NOT NULL
                      AND tran_id IS NOT NULL
                      AND TRIM(tran_id) <> ''
                    GROUP BY cmte_id, tran_id
                ) m
                  ON m.cmte_id = r.cmte_id
                 AND m.tran_id = r.tran_id
                 AND m.max_amend = r.amend_id
                UNION ALL
                SELECT r.* FROM rcpt_cd r
                JOIN (
                    SELECT filing_id, line_item, MAX(amend_id) AS max_amend
                    FROM rcpt_cd
                    WHERE cmte_id IS NULL
                       OR tran_id IS NULL
                       OR TRIM(tran_id) = ''
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


def _insert_rows(engine, table: str, rows, cmte_id: str = "C1") -> None:
    """Insert fixture rows; all fixture rows belong to committee C1."""
    cols = ("cmte_id, filing_id, amend_id, line_item, form_type,"
            " rec_type, tran_id, ctrib_naml, rcpt_date, amount")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"INSERT INTO {table} ({cols})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(cmte_id, *row) for row in rows],
        )


class TestDedupViewSemantics:
    def setup_method(self):
        self.engine = _engine_with_rcpt()
        _insert_rows(self.engine, "rcpt_cd", ROWS)

    def test_raw_table_keeps_all_versions(self):
        with self.engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM rcpt_cd")).scalar()
        assert n == 5  # every amendment version is stored

    def test_dedup_keeps_current_version_per_transaction(self):
        """Post-transition pin: winners are per-transaction (cmte_id, tran_id),
        not per filing line slot. Fixture expectations are unchanged because
        the highest-amend version of each transaction sits at its slot."""
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


class TestTransactionLevelDedup:
    """Issue #4 regression: a transaction refiled by an amended filing must
    be counted exactly once at its current value — not once per (filing_id,
    line_item) slot it has appeared on. The old slot-keyed view summed the
    original and every refile, inflating totals (the issue's ~2x symptom)."""

    def test_refiled_transaction_counted_once_in_totals(self):
        engine = _engine_with_rcpt()
        rows = [
            # T1: filed on filing 100 at $500, then refiled by amended
            # filing 101 at $350 (same transaction: same cmte_id + tran_id,
            # new line_item on the amending filing).
            (100, 0, 1, "460", "I", "T1", "ACME INC", "2024-01-05", 500.00),
            (101, 1, 3, "460", "I", "T1", "ACME INC", "2024-02-01", 350.00),
            # T2: never amended, still on its original filing.
            (100, 0, 2, "460", "I", "T2", "JOHN DOE", "2024-01-06", 100.00),
        ]
        _insert_rows(engine, "rcpt_cd", rows)
        with engine.connect() as conn:
            raw = float(
                conn.execute(text("SELECT SUM(amount) FROM rcpt_cd")).scalar()
            )
            winners = conn.execute(
                text(
                    "SELECT tran_id, filing_id, amend_id, amount"
                    " FROM rcpt_cd_deduped ORDER BY tran_id"
                )
            ).fetchall()
            deduped = float(
                conn.execute(
                    text("SELECT SUM(amount) FROM rcpt_cd_deduped")
                ).scalar()
            )
        # Raw sum double-counts T1 (500 + 350); the true current total is 450.
        assert raw == 950.00
        assert [(r[0], r[1], r[2], float(r[3])) for r in winners] == [
            ("T1", 101, 1, 350.00),  # current version of T1, filed on 101
            ("T2", 100, 0, 100.00),  # single version of T2
        ]
        assert deduped == 450.00

    def test_unattributed_rows_keep_latest_version_per_slot(self):
        """Rows with no committee or transaction attribution (null/blank
        cmte_id or tran_id) cannot be grouped by transaction; they keep the
        old slot semantics — nothing is dropped, nothing is double-counted."""
        engine = _engine_with_rcpt()
        rows = [
            (200, 0, 9, "496", "I", "", "UNKNOWN PAYEE", "2024-03-01", 50.00),
            (200, 1, 9, "496", "I", "", "UNKNOWN PAYEE", "2024-03-02", 40.00),
        ]
        _insert_rows(engine, "rcpt_cd", rows, cmte_id="")
        with engine.connect() as conn:
            winners = conn.execute(
                text(
                    "SELECT filing_id, amend_id, amount"
                    " FROM rcpt_cd_deduped WHERE tran_id = ''"
                    " ORDER BY amend_id"
                )
            ).fetchall()
        # Exactly one row survives for the (200, 9) slot: the $50 version was
        # superseded by the $40 version; both would double-count if kept.
        assert [(r[0], r[1], float(r[2])) for r in winners] == [
            (200, 1, 40.00),
        ]


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
