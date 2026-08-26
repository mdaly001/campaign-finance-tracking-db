"""Tests for the StateSourceAdapter (CAL-ACCESS ingestion)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.etl.adapter import SourceAdapter, SourceFileInfo
from state.adapter import StateDownload, StateSourceAdapter


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "state_cache"


@pytest.fixture
def adapter(cache_dir: Path) -> StateSourceAdapter:
    return StateSourceAdapter(cache_dir=cache_dir)


# -- Inheritance and interface checks -------------------------------------- #


def test_adapter_inherits_source_adapter(adapter: StateSourceAdapter) -> None:
    """StateSourceAdapter must subclass SourceAdapter."""
    assert isinstance(adapter, SourceAdapter)


def test_adapter_source_attribute(adapter: StateSourceAdapter) -> None:
    """The source class attribute must be 'state'."""
    assert adapter.source == "state"


# -- compute_checksum ------------------------------------------------------ #


def test_compute_checksum_known_input(adapter: StateSourceAdapter) -> None:
    """compute_checksum returns correct SHA-256 for known bytes."""
    raw = b"hello world"
    expected = hashlib.sha256(raw).hexdigest()
    assert adapter.compute_checksum(raw) == expected


def test_compute_checksum_empty(adapter: StateSourceAdapter) -> None:
    """compute_checksum handles empty bytes."""
    assert adapter.compute_checksum(b"") == hashlib.sha256(b"").hexdigest()


# -- cache helpers --------------------------------------------------------- #


def test_get_cached_zip_missing(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """_get_cached_zip returns None when no zip file exists."""
    assert adapter._get_cached_zip() is None


def test_get_cached_zip_present(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """_get_cached_zip returns the Path when the zip file exists."""
    zip_path = cache_dir / "dbwebexport.zip"
    zip_path.write_bytes(b"fake zip")
    assert adapter._get_cached_zip() == zip_path


# -- get_source_files (mocked download) ------------------------------------ #


def test_get_source_files_returns_source_file_info(
    cache_dir: Path, adapter: StateSourceAdapter
) -> None:
    """get_source_files returns a list of SourceFileInfo objects.

    We mock the download step because the real SOS endpoint may be
    slow or unavailable in CI.
    """
    fake_tsv_entries = {
        "RCPT_CD.tsv": b"col1\tcol2\na\tb\n",
        "CandNomv18_22.tsv": b"col1\tcol2\nx\ty\n",
        "README.txt": b"not a tsv",
    }

    with (
        patch.object(adapter, "_download_zip") as mock_dl,
        patch("zipfile.ZipFile") as mock_zipfile,
    ):
        # _download_zip returns a fake StateDownload
        fake_dl = MagicMock()
        fake_dl.path = cache_dir / "dbwebexport.zip"
        fake_dl.checksum = "abc123"
        mock_dl.return_value = fake_dl

        # zipfile context manager
        mock_zf = MagicMock()
        mock_zf.namelist.return_value = list(fake_tsv_entries.keys())
        mock_zipfile.return_value.__enter__.return_value = mock_zf

        files = adapter.get_source_files()

    assert isinstance(files, list)
    assert len(files) == 2  # Only .tsv files
    for f in files:
        assert isinstance(f, SourceFileInfo)
        assert f.name.endswith(".tsv")

    names = {f.name for f in files}
    assert names == {"CandNomv18_22.tsv", "RCPT_CD.tsv"}


def test_get_source_files_sorted(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """get_source_files returns files sorted alphabetically by name."""
    fake_tsv_entries = {
        "Z_LAST.tsv": b"a\tb\n",
        "A_FIRST.tsv": b"c\td\n",
        "M_MIDDLE.tsv": b"e\tf\n",
    }

    with (
        patch.object(adapter, "_download_zip") as mock_dl,
        patch("zipfile.ZipFile") as mock_zipfile,
    ):
        fake_dl = MagicMock()
        fake_dl.path = cache_dir / "dbwebexport.zip"
        fake_dl.checksum = "abc123"
        mock_dl.return_value = fake_dl

        mock_zf = MagicMock()
        mock_zf.namelist.return_value = list(fake_tsv_entries.keys())
        mock_zipfile.return_value.__enter__.return_value = mock_zf

        files = adapter.get_source_files()

    names = [f.name for f in files]
    assert names == ["A_FIRST.tsv", "M_MIDDLE.tsv", "Z_LAST.tsv"]


# -- fetch_file (mocked) --------------------------------------------------- #


def test_fetch_file_returns_bytes(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """fetch_file extracts raw bytes from the cached zip."""
    mock_content = b"col1\tcol2\nval1\tval2\n"
    fake_info = SourceFileInfo(name="RCPT_CD.tsv", url="http://fake")

    with (
        patch.object(adapter, "_download_zip") as mock_dl,
        patch("zipfile.ZipFile") as mock_zipfile,
    ):
        fake_dl = MagicMock()
        fake_dl.path = cache_dir / "dbwebexport.zip"
        fake_dl.checksum = "abc123"
        mock_dl.return_value = fake_dl

        mock_zf = MagicMock()
        mock_zf.namelist.return_value = ["RCPT_CD.tsv"]
        mock_zf.read.return_value = mock_content
        mock_zipfile.return_value.__enter__.return_value = mock_zf

        result = adapter.fetch_file(fake_info)

    assert result == mock_content
    mock_zf.read.assert_called_once_with("RCPT_CD.tsv")


def test_fetch_file_missing_raises(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """fetch_file raises FileNotFoundError for missing file."""
    fake_info = SourceFileInfo(name="MISSING.tsv", url="http://fake")

    with (
        patch.object(adapter, "_download_zip") as mock_dl,
        patch("zipfile.ZipFile") as mock_zipfile,
    ):
        fake_dl = MagicMock()
        fake_dl.path = cache_dir / "dbwebexport.zip"
        fake_dl.checksum = "abc123"
        mock_dl.return_value = fake_dl

        mock_zf = MagicMock()
        mock_zf.namelist.return_value = ["RCPT_CD.tsv"]
        mock_zipfile.return_value.__enter__.return_value = mock_zf

        with pytest.raises(FileNotFoundError, match="MISSING.tsv"):
            adapter.fetch_file(fake_info)


# -- parse_file ------------------------------------------------------------ #


def test_parse_file_returns_iterable_of_dicts(adapter: StateSourceAdapter) -> None:
    """parse_file yields dicts from raw TSV bytes."""
    raw_tsv = b"col1\tcol2\na\tb\nc\td\n"
    result = list(adapter.parse_file(raw_tsv))
    assert len(result) == 2
    assert result[0] == {"col1": "a", "col2": "b"}
    assert result[1] == {"col1": "c", "col2": "d"}


def test_parse_file_empty_to_none(adapter: StateSourceAdapter) -> None:
    """Empty fields are converted to None."""
    raw_tsv = b"col1\tcol2\na\t\n\td\n"
    result = list(adapter.parse_file(raw_tsv))
    assert result[0]["col2"] is None
    assert result[1]["col1"] is None


# -- upsert_records -------------------------------------------------------- #


def test_upsert_records_empty(adapter: StateSourceAdapter) -> None:
    """upsert_records returns LoadSummary with zero rows for empty input."""
    summary = adapter.upsert_records(iter([]), MagicMock())
    assert summary.rows_read == 0
    assert summary.rows_upserted == 0


def test_upsert_records_with_conflict_cols(
    adapter: StateSourceAdapter,
) -> None:
    """upsert_records calls upsert_records from core.etl when conflict cols present."""
    mock_session = MagicMock()
    records = [
        {
            "__table__": "rcpt_cd",
            "__conflict_cols__": ["id"],
            "id": "1",
            "amount": "100",
        }
    ]

    with patch("core.etl.upsert.upsert_records") as mock_upsert:
        mock_upsert.return_value = 1
        summary = adapter.upsert_records(iter(records), mock_session)

    assert summary.rows_read == 1
    assert summary.rows_upserted == 1
    mock_upsert.assert_called_once_with(mock_session, "rcpt_cd", records, ["id"])


# -- is_up_to_date (mocked) ----------------------------------------------- #


def test_is_up_to_date_false_when_no_cache(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """is_up_to_date returns False when nothing is cached."""
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"remote data"
        mock_client.return_value.__enter__.return_value.head.return_value = mock_resp

        assert adapter.is_up_to_date() is False


def _write_sidecar(cache_dir: Path, **fields) -> None:
    """Write the dbwebexport.zip.meta sidecar with the given identity fields."""
    import json

    sidecar = cache_dir / "dbwebexport.zip.meta"
    sidecar.write_text(json.dumps(fields))


def test_is_up_to_date_true_when_etag_matches(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """is_up_to_date returns True when the CDN etag matches the sidecar."""
    cached_zip = cache_dir / "dbwebexport.zip"
    cached_zip.write_bytes(b"same content")
    _write_sidecar(
        cache_dir,
        sha256=hashlib.sha256(b"same content").hexdigest(),
        size_bytes=len(b"same content"),
        etag='"abc-123"',
        content_length=len(b"same content"),
    )

    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"etag": '"abc-123"'}
        mock_client.return_value.__enter__.return_value.head.return_value = mock_resp

        assert adapter.is_up_to_date() is True


def test_is_up_to_date_false_when_etag_differs(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """is_up_to_date returns False when the CDN etag differs from the sidecar."""
    cached_zip = cache_dir / "dbwebexport.zip"
    cached_zip.write_bytes(b"same content")
    _write_sidecar(
        cache_dir,
        sha256=hashlib.sha256(b"same content").hexdigest(),
        size_bytes=len(b"same content"),
        etag='"old-etag"',
        content_length=len(b"same content"),
    )

    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"etag": '"new-etag"'}
        mock_client.return_value.__enter__.return_value.head.return_value = mock_resp

        assert adapter.is_up_to_date() is False


def test_is_up_to_date_true_when_last_modified_matches(
    cache_dir: Path, adapter: StateSourceAdapter
) -> None:
    """Without an etag, a matching last-modified header counts as fresh."""
    cached_zip = cache_dir / "dbwebexport.zip"
    cached_zip.write_bytes(b"same content")
    _write_sidecar(
        cache_dir,
        sha256=hashlib.sha256(b"same content").hexdigest(),
        size_bytes=len(b"same content"),
        last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
        content_length=len(b"same content"),
    )

    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"last-modified": "Mon, 01 Jan 2024 00:00:00 GMT"}
        mock_client.return_value.__enter__.return_value.head.return_value = mock_resp

        assert adapter.is_up_to_date() is True


def test_is_up_to_date_conservative_without_identity_headers(
    cache_dir: Path, adapter: StateSourceAdapter
) -> None:
    """When neither side carries comparable identity headers, assume stale."""
    cached_zip = cache_dir / "dbwebexport.zip"
    cached_zip.write_bytes(b"some content")
    _write_sidecar(
        cache_dir,
        sha256=hashlib.sha256(b"some content").hexdigest(),
        size_bytes=len(b"some content"),
    )

    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_client.return_value.__enter__.return_value.head.return_value = mock_resp

        assert adapter.is_up_to_date() is False


# -- clear_cache ----------------------------------------------------------- #


def test_clear_cache_removes_zip(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """clear_cache removes the cached dbwebexport.zip."""
    zip_path = cache_dir / "dbwebexport.zip"
    zip_path.write_bytes(b"some data")
    assert zip_path.exists()

    adapter.clear_cache()
    assert not zip_path.exists()


def test_clear_cache_noop_when_missing(cache_dir: Path, adapter: StateSourceAdapter) -> None:
    """clear_cache does nothing when no zip exists."""
    # Should not raise
    adapter.clear_cache()


# -- StateDownload dataclass ----------------------------------------------- #


def test_state_download_dataclass(cache_dir: Path) -> None:
    """StateDownload is a valid dataclass."""
    sd = StateDownload(
        path=cache_dir / "test.zip",
        checksum="abc",
        size_bytes=1024,
        last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
    )
    assert sd.path == cache_dir / "test.zip"
    assert sd.checksum == "abc"
    assert sd.size_bytes == 1024
    assert sd.last_modified == "Mon, 01 Jan 2024 00:00:00 GMT"
