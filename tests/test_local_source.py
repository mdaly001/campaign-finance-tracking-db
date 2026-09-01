"""Tests for local on-disk source ingestion (LocalSourceAdapter + runners).

These tests never touch the network: the local adapter is exercised against
tiny on-disk TSV files, and the runners are driven with an injected
adapter (or ``source_dir``) against an in-memory SQLite engine.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import state.etl as etl
from state.adapter import LocalSourceAdapter, SourceFileInfo
from state.tables import TableDefinition


def _write_tsv(root: Path, name: str, header: list[str], rows: list[list]) -> Path:
    """Write a tiny TSV into *root* and return its path."""
    path = root / name
    body = "\t".join(header) + "\n"
    for row in rows:
        body += "\t".join(str(x) for x in row) + "\n"
    path.write_text(body)
    return path


# ------------------------------------------------------------------ #
#  LocalSourceAdapter
# ------------------------------------------------------------------ #


class TestLocalSourceAdapter:
    def test_accepts_export_root_and_data_dir(self, tmp_path: Path) -> None:
        """Both the export root and its DATA subdir are accepted."""
        (tmp_path / "CalAccess" / "DATA").mkdir(parents=True)
        root = LocalSourceAdapter(tmp_path / "CalAccess")
        data = LocalSourceAdapter(tmp_path / "CalAccess" / "DATA")
        assert isinstance(root.data_dir, Path)
        assert root.data_dir == data.data_dir == tmp_path / "CalAccess" / "DATA"

    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            LocalSourceAdapter(tmp_path / "does-not-exist")

    def test_lists_and_fetches_files(self, tmp_path: Path) -> None:
        data = tmp_path / "DATA"
        data.mkdir()
        p = _write_tsv(data, "FILERNAME_CD.TSV", ["id", "filer_id", "namf"], [[1, 101, "A"]])

        adapter = LocalSourceAdapter(data)
        infos = adapter.get_source_files()
        assert [i.name for i in infos] == ["FILERNAME_CD.TSV"]
        assert isinstance(infos[0], SourceFileInfo)
        assert infos[0].url == f"file://{p}"
        assert adapter.fetch_file(infos[0]) == p.read_bytes()

    def test_fetch_unknown_file_raises(self, tmp_path: Path) -> None:
        data = tmp_path / "DATA"
        data.mkdir()
        adapter = LocalSourceAdapter(data)
        adapter.get_source_files()  # builds the path index
        with pytest.raises(FileNotFoundError):
            adapter.fetch_file(SourceFileInfo(name="MISSING_CD.TSV", url="file:///missing"))

    def test_source_checksum_stable_and_changes(self, tmp_path: Path) -> None:
        data = tmp_path / "DATA"
        data.mkdir()
        _write_tsv(data, "SMRY_CD.TSV", ["filing_id", "amount"], [[1, 500]])

        adapter = LocalSourceAdapter(data)
        c1 = adapter.source_checksum()
        assert c1 is not None and c1 == adapter.source_checksum()

        # Adding a new file changes the checksum (new content to load).
        _write_tsv(data, "LOAN_CD.TSV", ["filing_id", "amount"], [[2, 1000]])
        c2 = adapter.source_checksum()
        assert c2 is not None and c2 != c1

    def test_is_up_to_date_and_refresh_noop(self, tmp_path: Path) -> None:
        data = tmp_path / "DATA"
        data.mkdir()
        adapter = LocalSourceAdapter(data)
        assert adapter.is_up_to_date() is True
        adapter.refresh()  # must not raise / hit network
        assert adapter.source_checksum() is None  # empty dir → no checksum


# ------------------------------------------------------------------ #
#  Runners with injected local adapter
# ------------------------------------------------------------------ #

_DDL = """
CREATE TABLE load_checkpoint (
    checkpoint_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name     VARCHAR(50) NOT NULL,
    source         VARCHAR(30) NOT NULL DEFAULT 'calaccess',
    file_hash      VARCHAR(64) NOT NULL,
    source_file    VARCHAR(200),
    processed_date TIMESTAMP,
    rows_processed INTEGER,
    notes          TEXT,
    UNIQUE(table_name, source, file_hash)
)
"""


def _make_engine(url: str | None = None):
    """Engine over an in-memory or file-backed SQLite DB (shared by the runner).

    With an in-memory SQLite URL each engine instance is its own private
    database, so runner tests must share a URL instead: pass ``url`` (the
    runner creates its own engine from the same URL).
    """
    engine = create_engine(url or "sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_DDL))
    return engine


def _create_tables(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE acronyms_cd (acronym TEXT PRIMARY KEY, meaning TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE filername_cd (id INTEGER PRIMARY KEY, filer_id INTEGER, naml TEXT)"
            )
        )


@pytest.fixture()
def fake_table_defs(monkeypatch: pytest.MonkeyPatch) -> dict:
    defs = {
        "ACRONYMS_CD": TableDefinition(
            code="ACRONYMS_CD",
            description="test acronyms",
            category="dimension",
            tsv_files=["ACRONYMS_CD.TSV"],
            conflict_columns=["acronym"],
        ),
        "FILERNAME_CD": TableDefinition(
            code="FILERNAME_CD",
            description="test filernames",
            category="dimension",
            tsv_files=["FILERNAME_CD.TSV"],
            conflict_columns=["id"],
        ),
    }
    monkeypatch.setattr(etl, "TABLE_DEFINITIONS", defs)
    return defs


@pytest.fixture()
def local_data(tmp_path: Path) -> Path:
    data = tmp_path / "CalAccess" / "DATA"
    data.mkdir(parents=True)
    _write_tsv(
        data,
        "ACRONYMS_CD.TSV",
        ["acronym", "meaning"],
        [["ACRONYM", "a word"], ["CAL", "California"]],
    )
    _write_tsv(
        data,
        "FILERNAME_CD.TSV",
        ["id", "filer_id", "naml"],
        [[1, 101, "Foo"], [2, 102, "Bar"]],
    )
    return tmp_path / "CalAccess"


def _create_tables(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE acronyms_cd (acronym TEXT PRIMARY KEY, meaning TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE filername_cd (id INTEGER PRIMARY KEY, filer_id INTEGER, naml TEXT)"
            )
        )


class TestFullLoadRunnerLocalAdapter:
    def test_full_load_from_injected_local_adapter(
        self, local_data: Path, fake_table_defs, tmp_path: Path
    ) -> None:
        db_url = f"sqlite:///{tmp_path / 'full.db'}"
        engine = _make_engine(db_url)
        _create_tables(engine)

        adapter = LocalSourceAdapter(local_data)
        runner = etl.FullLoadRunner(
            db_url,
            cache_dir=None,
            batch_size=10,
            watchdog=False,
            adapter=adapter,
        )
        result = runner.run(table_order=["ACRONYMS_CD", "FILERNAME_CD"])

        assert result.tables_loaded == 2
        assert result.total_rows_upserted == 4
        with engine.connect() as conn:
            n1 = conn.execute(text("SELECT COUNT(*) FROM acronyms_cd")).scalar_one()
            n2 = conn.execute(text("SELECT COUNT(*) FROM filername_cd")).scalar_one()
        assert n1 == 2
        assert n2 == 2

    def test_unknown_table_code_skipped(self, local_data: Path, fake_table_defs) -> None:
        """A code without a source file is skipped, not crashed on."""
        engine = _make_engine()
        _create_tables(engine)

        runner = etl.FullLoadRunner(
            database_url="sqlite://",
            cache_dir=None,
            adapter=LocalSourceAdapter(local_data),
        )
        # FILING_CALENDAR has no local TSV → skipped with reason tsv_not_found
        result = runner.run(tables_only=["FILING_CALENDAR"])
        assert result.tables_skipped == 1
        assert result.tables[0]["reason"] == "tsv_not_found"

    def test_explicit_adapter_beats_source_dir(
        self, local_data: Path, fake_table_defs, monkeypatch
    ) -> None:
        """An explicitly injected adapter always wins over source_dir."""
        engine = _make_engine()
        _create_tables(engine)

        sentinel = LocalSourceAdapter(local_data)
        calls = {"n": 0}
        original = sentinel.get_source_files

        def spy():
            calls["n"] += 1
            return original()

        monkeypatch.setattr(sentinel, "get_source_files", spy)
        runner = etl.FullLoadRunner(
            "sqlite://",
            cache_dir=None,
            adapter=sentinel,
            source_dir=local_data / "does-not-exist",  # would raise if used
        )
        runner.run(table_order=["ACRONYMS_CD"])
        assert calls["n"] == 1


class TestResumeRunner:
    def test_resume_loads_never_loaded_tables(
        self, local_data: Path, fake_table_defs, tmp_path: Path
    ) -> None:
        """Regression: resume must load tables that were never loaded before.

        Previously ``get_unchecked_tables(<zip-hash>)`` only returned tables
        that already had checkpoints (stale hashes) — never-loaded tables
        were absent from the result, so resume skipped them forever.
        """
        db_url = f"sqlite:///{tmp_path / 'resume.db'}"
        engine = _make_engine(db_url)
        _create_tables(engine)

        # Seed a checkpoint for the *first* run's file hashes (the file on
        # disk is the current version) so that table A is "already loaded".
        from core.etl.checkpoint import LoadCheckpoint

        cp = LoadCheckpoint(engine)
        a_path = local_data / "DATA" / "ACRONYMS_CD.TSV"
        cp.load_checkpoint(
            "acronyms_cd",
            hashlib.sha256(a_path.read_bytes()).hexdigest(),
        )

        runner = etl.ResumeRunner(
            db_url,
            cache_dir=None,
            batch_size=10,
            adapter=LocalSourceAdapter(local_data),
        )
        result = runner.run(table_order=["ACRONYMS_CD", "FILERNAME_CD"])

        # ACRONYMS_CD: skipped (checkpoint hash matches current file).
        # FILERNAME_CD: has never been loaded → must load now (the bug).
        assert result.tables_loaded == 1
        assert any(
            t["code"] == "FILERNAME_CD" and t["status"] == "loaded"
            for t in result.tables
        )
        assert any(
            t["code"] == "ACRONYMS_CD"
            and t["status"] == "skipped"
            and t["reason"] == "already_loaded"
            for t in result.tables
        )
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT COUNT(*) FROM acronyms_cd")
            ).scalar_one() == 0
            assert conn.execute(
                text("SELECT COUNT(*) FROM filername_cd")
            ).scalar_one() == 2
