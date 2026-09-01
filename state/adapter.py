"""StateSourceAdapter for California SOS CAL-ACCESS data ingestion.

Downloads dbwebexport.zip from SOS CDN, extracts TSV file list,
computes checksums for incremental detection.

Also provides ``LocalSourceAdapter`` — a drop-in source that reads TSV
files from an already-extracted local copy of the export (e.g. an
unzipped ``CalAccess/DATA`` directory) so local/testing ETL runs need
no network download at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.etl.adapter import LoadSummary, SourceAdapter, SourceFileInfo
from core.etl.tsv import TSVReader

logger = logging.getLogger(__name__)

# Configuration — overridable via environment variables
SOS_RAW_DATA_URL = os.getenv(
    "SOS_RAW_DATA_URL",
    "https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip",
)
STATE_CACHE_DIR = Path(os.getenv("STATE_CACHE_DIR", "/app/state/cache"))


@dataclass
class StateDownload:
    """Represents a downloaded zip file with metadata."""

    path: Path
    checksum: str
    size_bytes: int
    last_modified: str | None


class StateSourceAdapter(SourceAdapter):
    """Download and parse California SOS CAL-ACCESS raw data.

    Downloads dbwebexport.zip from SOS CDN, extracts TSV files,
    computes checksums for incremental detection.
    """

    source = "state"

    def __init__(
        self,
        cache_dir: Path | None = None,
        refresh: bool = False,
    ) -> None:
        """
        Args:
            cache_dir: Directory for the cached dbwebexport.zip.
            refresh: If True, always re-download the zip on the first
                access (ignoring any on-disk cache).
        """
        self.cache_dir = cache_dir or STATE_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.reader = TSVReader(has_header=True, empty_to_none=True)
        self.force_refresh = refresh
        self._cached_file: StateDownload | None = None

    # -- cache helpers ----------------------------------------------------- #

    @property
    def _zip_path(self) -> Path:
        return self.cache_dir / "dbwebexport.zip"

    @property
    def _meta_path(self) -> Path:
        """Sidecar metadata (sha256/size/headers) written on download."""
        return self.cache_dir / "dbwebexport.zip.meta"

    def _get_cached_zip(self) -> Path | None:
        """Return cached zip path if it exists, None otherwise."""
        return self._zip_path if self._zip_path.exists() else None

    def _load_cached_state(self) -> StateDownload | None:
        """Build StateDownload from the on-disk cache + sidecar metadata.

        If the sidecar is missing (e.g. a manually placed zip), computes
        the checksum once and writes it.
        """
        zip_path = self._get_cached_zip()
        if zip_path is None:
            return None
        meta: dict[str, Any] = {}
        if self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                meta = {}
        checksum = meta.get("sha256")
        if not checksum:
            h = hashlib.sha256()
            with open(zip_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            checksum = h.hexdigest()
            meta["sha256"] = checksum
            # Record size so is_up_to_date() can make a size-based
            # freshness judgement even without CDN identity headers.
            meta.setdefault("content_length", zip_path.stat().st_size)
            try:
                self._meta_path.write_text(json.dumps(meta))
            except OSError:
                pass
        return StateDownload(
            path=zip_path,
            checksum=checksum,
            size_bytes=meta.get("size_bytes", zip_path.stat().st_size),
            last_modified=meta.get("last_modified"),
        )

    def _download_zip(self) -> StateDownload:
        """Download the latest dbwebexport.zip from SOS CDN."""
        zip_path = self._zip_path
        meta_path = self._meta_path

        with httpx.Client(timeout=300.0) as client:
            response = client.get(SOS_RAW_DATA_URL)
            response.raise_for_status()

            raw_bytes = response.content
            checksum = hashlib.sha256(raw_bytes).hexdigest()
            last_modified = response.headers.get("last-modified")
            size = len(raw_bytes)

            zip_path.write_bytes(raw_bytes)
            meta = {
                "sha256": checksum,
                "size_bytes": size,
                "last_modified": last_modified,
                "etag": response.headers.get("etag"),
                "content_length": int(response.headers.get("content-length") or 0),
            }
            try:
                meta_path.write_text(json.dumps(meta))
            except OSError:
                pass

            logger.info(
                "Downloaded dbwebexport.zip: %d bytes, sha256=%s, last-modified=%s",
                size,
                checksum,
                last_modified,
            )

            return StateDownload(
                path=zip_path,
                checksum=checksum,
                size_bytes=size,
                last_modified=last_modified,
            )

    def _ensure_zip(self) -> StateDownload:
        """Return a usable cached zip, downloading when needed."""
        if self._cached_file is not None and self._cached_file.path.exists():
            return self._cached_file
        if not self.force_refresh:
            cached = self._load_cached_state()
            if cached is not None:
                logger.info(
                    "Reusing cached dbwebexport.zip: %s (%d bytes)",
                    cached.path,
                    cached.size_bytes,
                )
                self._cached_file = cached
                return cached
        self._cached_file = self._download_zip()
        return self._cached_file

    # -- SourceAdapter interface ------------------------------------------- #

    def get_source_files(self) -> list[SourceFileInfo]:
        """Get list of all TSV files in the latest download.

        Uses the on-disk cache when present (download only when missing
        or when the adapter was created with refresh=True), then lists
        all .TSV entries from the zip's central directory without full
        extraction.
        """
        cached = self._ensure_zip()
        zip_path = cached.path
        checksum = cached.checksum

        tsv_files: list[SourceFileInfo] = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                # Real export entries are upper-case ".TSV" — match case-insensitively.
                if name.lower().endswith(".tsv"):
                    tsv_files.append(
                        SourceFileInfo(
                            name=name,
                            url=SOS_RAW_DATA_URL,
                            checksum=f"{checksum}:{name}",
                        )
                    )

        logger.info("Found %d TSV files in dbwebexport.zip", len(tsv_files))
        return sorted(tsv_files, key=lambda f: f.name)

    def fetch_file(self, info: SourceFileInfo) -> bytes:
        """Extract a single TSV file from the cached zip.

        Returns the raw bytes of the TSV file.
        """
        zip_path = self._ensure_zip().path

        with zipfile.ZipFile(zip_path, "r") as zf:
            if info.name not in zf.namelist():
                raise FileNotFoundError(f"File {info.name} not found in zip")
            raw_bytes = zf.read(info.name)
            logger.debug("Extracted %s: %d bytes", info.name, len(raw_bytes))
            return raw_bytes

    def parse_file(self, raw: bytes) -> Iterator[dict[str, Any]]:
        """Parse raw TSV bytes into an iterator of dicts.

        Delegates to TSVReader.
        """
        return iter(self.reader.read_bytes(raw))

    def upsert_records(
        self,
        records: Iterator[dict[str, Any]],
        session: Any,
    ) -> LoadSummary:
        """Upsert parsed records into the database.

        Uses the generic upsert_records function from core.etl.upsert.
        """
        from core.etl.upsert import upsert_records

        records_list = list(records)

        if records_list:
            table_name = records_list[0].get("__table__", "unknown")
            conflict_columns = records_list[0].get("__conflict_cols__", [])

            if conflict_columns:
                upserted = upsert_records(
                    session,
                    table_name,
                    records_list,
                    conflict_columns,
                )
                return LoadSummary(
                    rows_read=len(records_list),
                    rows_upserted=upserted,
                )

        return LoadSummary(rows_read=len(records_list))

    def compute_checksum(self, raw: bytes) -> str:
        """Compute SHA-256 checksum of raw bytes."""
        return hashlib.sha256(raw).hexdigest()

    def source_checksum(self) -> str | None:
        """Stable checksum of the current source (zip-level, not per-file).

        For the remote adapter this is the cached zip's SHA-256 once the
        zip has been fetched/cached, or ``None`` before the first fetch.
        Runners use it as the checkpoint grouping key for a full load.
        """
        if self._cached_file is not None:
            return self._cached_file.checksum
        return None

    # -- incremental helpers ----------------------------------------------- #

    def refresh(self) -> None:
        """Force a fresh download on the next access (discard local cache)."""
        self.force_refresh = True
        self._cached_file = None

    def is_up_to_date(self) -> bool:
        """Check if the cached zip matches the current remote version.

        Issues a HEAD request and compares the remote ETag /
        Last-Modified / Content-Length against the sidecar metadata that
        was recorded when the zip was downloaded. Returns True if the
        cached copy is current (no re-download needed).

        Note: the SOS CDN does not reliably expose a checksum, so we rely
        on the identity headers. If the remote is unreachable or returns
        no identity headers, we conservatively report False so the caller
        re-downloads.
        """
        cached_path = self._get_cached_zip()
        if cached_path is None:
            return False

        meta: dict[str, Any] = {}
        if self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                meta = {}
        if not meta:
            # Cache exists but has no recorded identity — treat as stale.
            return False

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.head(SOS_RAW_DATA_URL, follow_redirects=True)
                response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("HEAD check failed (%s) — assuming stale", e)
            return False

        remote_etag = response.headers.get("etag")
        remote_lm = response.headers.get("last-modified")
        remote_len = int(response.headers.get("content-length") or 0)

        # Compare whichever identity fields both sides have.
        if meta.get("etag") and remote_etag:
            return meta["etag"] == remote_etag
        if meta.get("last_modified") and remote_lm:
            return meta["last_modified"] == remote_lm
        if meta.get("content_length") and remote_len:
            return meta["content_length"] == remote_len

        # No comparable identity headers — conservatively assume stale.
        logger.debug("No comparable identity headers — assuming stale")
        return False

    def clear_cache(self) -> None:
        """Clear the cached download."""
        zip_path = self.cache_dir / "dbwebexport.zip"
        if zip_path.exists():
            zip_path.unlink()
            logger.info("Cleared cached dbwebexport.zip")


class LocalSourceAdapter(SourceAdapter):
    """Read TSV source files from an already-extracted local export.

    Drop-in replacement for :class:`StateSourceAdapter` when the raw
    ``CalAccess/DATA`` export already lives on disk (dev/testing runs,
    air-gapped machines, re-runs after a previous download). No network
    access happens: ``get_source_files`` lists the ``*.TSV`` files under
    ``data_dir`` (or ``data_dir/DATA`` when the export root is given)
    and ``fetch_file`` reads them straight from disk.

    Example::

        from state.adapter import LocalSourceAdapter
        from state.etl import FullLoadRunner

        runner = FullLoadRunner(
            database_url="postgresql://cfdb:cfdb@localhost:5432/cfdb",
            adapter=LocalSourceAdapter("/data/CalAccess"),
        )
        runner.run()
    """

    source = "local"

    def __init__(self, data_dir: Path | str) -> None:
        """
        Args:
            data_dir: Directory holding the extracted export. Either the
                export root (containing a ``DATA`` subdir) or the ``DATA``
                directory itself — both are accepted.
        """
        root = Path(data_dir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Local source directory not found: {root}")
        if (root / "DATA").is_dir():
            root = root / "DATA"
        self.data_dir = root
        self.reader = TSVReader(has_header=True, empty_to_none=True)
        self._paths: dict[str, Path] | None = None

    # -- SourceAdapter interface -------------------------------------------- #

    def get_source_files(self) -> list[SourceFileInfo]:
        """List every ``*.TSV`` file (case-insensitive) under the data dir."""
        self._paths = {}
        infos: list[SourceFileInfo] = []
        for path in sorted(self.data_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".tsv":
                continue
            info = SourceFileInfo(
                name=path.name,
                url=f"file://{path}",
                checksum=self._file_checksum(path),
                size=path.stat().st_size,
            )
            self._paths[path.name.lower()] = path
            infos.append(info)
        logger.info(
            "Local source: found %d TSV files under %s", len(infos), self.data_dir
        )
        return sorted(infos, key=lambda f: f.name)

    def fetch_file(self, info: SourceFileInfo) -> bytes:
        """Read a single TSV file from disk and return its raw bytes."""
        path = self._resolve(info.name)
        raw_bytes = path.read_bytes()
        logger.debug("Read %s from local dir: %d bytes", path.name, len(raw_bytes))
        return raw_bytes

    def parse_file(self, raw: bytes) -> Iterator[dict[str, Any]]:
        """Parse raw TSV bytes into an iterator of dicts (TSVReader)."""
        return iter(self.reader.read_bytes(raw))

    def upsert_records(
        self,
        records: Iterator[dict[str, Any]],
        session: Any,
    ) -> LoadSummary:
        """Upsert parsed records (delegates to core.etl.upsert.upsert_records)."""
        from core.etl.upsert import upsert_records

        records_list = list(records)
        if records_list:
            table_name = records_list[0].get("__table__", "unknown")
            conflict_columns = records_list[0].get("__conflict_cols__", [])
            if conflict_columns:
                upserted = upsert_records(
                    session,
                    table_name,
                    records_list,
                    conflict_columns,
                )
                return LoadSummary(rows_read=len(records_list), rows_upserted=upserted)
        return LoadSummary(rows_read=len(records_list))

    def compute_checksum(self, raw: bytes) -> str:
        """Compute SHA-256 checksum of raw bytes."""
        return hashlib.sha256(raw).hexdigest()

    # -- local-specific helpers --------------------------------------------- #

    def is_up_to_date(self) -> bool:
        """Local sources are up to date by definition (nothing to refresh)."""
        return True

    def refresh(self) -> None:
        """No-op: a local source has no remote to re-fetch from."""
        logger.info("LocalSourceAdapter.refresh() is a no-op (source is on disk)")

    def source_checksum(self) -> str | None:
        """Stable digest over the file inventory (name + size + mtime).

        Stable across runs as long as no file on disk changed — so
        checkpoint grouping is deterministic for the same data set and a
        re-run with modified files produces fresh checkpoint entries.
        """
        paths = self._all_files()
        if not paths:
            return None
        h = hashlib.sha256()
        for path in paths:
            stat = path.stat()
            h.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        return h.hexdigest()

    # -- internals ----------------------------------------------------------- #

    def _file_checksum(self, path: Path) -> str:
        """Content-addressed checksum for one source file."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _all_files(self) -> list[Path]:
        """All .tsv files in the data dir, case-insensitive, sorted by name."""
        return sorted(
            (
                p
                for p in self.data_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".tsv"
            ),
            key=lambda p: p.name,
        )

    def _resolve(self, name: str) -> Path:
        """Resolve a source-file name to a path on disk (case-insensitive)."""
        if self._paths is None:
            self.get_source_files()
        path = (self._paths or {}).get(name.lower())
        if path is not None:
            return path
        # Fallback: direct path lookup (case-sensitive) for ad-hoc infos
        direct = self.data_dir / name
        if direct.is_file():
            return direct
        raise FileNotFoundError(f"File {name} not found in local source dir {self.data_dir}")
