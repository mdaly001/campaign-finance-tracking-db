"""StateSourceAdapter for California SOS CAL-ACCESS data ingestion.

Downloads dbwebexport.zip from SOS CDN, extracts TSV file list,
computes checksums for incremental detection.
"""

from __future__ import annotations

import hashlib
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

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or STATE_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.reader = TSVReader(has_header=True, empty_to_none=True)
        self._cached_file: StateDownload | None = None

    # -- cache helpers ----------------------------------------------------- #

    def _get_cached_zip(self) -> Path | None:
        """Return cached zip path if it exists, None otherwise."""
        zip_path = self.cache_dir / "dbwebexport.zip"
        return zip_path if zip_path.exists() else None

    def _download_zip(self) -> StateDownload:
        """Download the latest dbwebexport.zip from SOS CDN."""
        zip_path = self.cache_dir / "dbwebexport.zip"

        with httpx.Client(timeout=300.0) as client:
            response = client.get(SOS_RAW_DATA_URL)
            response.raise_for_status()

            raw_bytes = response.content
            checksum = hashlib.sha256(raw_bytes).hexdigest()
            last_modified = response.headers.get("last-modified")
            size = len(raw_bytes)

            zip_path.write_bytes(raw_bytes)

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

    # -- SourceAdapter interface ------------------------------------------- #

    def get_source_files(self) -> list[SourceFileInfo]:
        """Get list of all TSV files in the latest download.

        Downloads the zip if not cached, then lists all .tsv entries
        from the zip's central directory without full extraction.
        """
        if self._cached_file and self._cached_file.path.exists():
            zip_path = self._cached_file.path
            checksum = self._cached_file.checksum
        else:
            self._cached_file = self._download_zip()
            zip_path = self._cached_file.path
            checksum = self._cached_file.checksum

        tsv_files: list[SourceFileInfo] = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".tsv"):
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
        zip_path = self.cache_dir / "dbwebexport.zip"

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

    # -- incremental helpers ----------------------------------------------- #

    def is_up_to_date(self) -> bool:
        """Check if the cached zip is the latest version.

        Compares the cached checksum with the latest remote checksum.
        Returns True if no update needed.
        """
        with httpx.Client(timeout=30.0) as client:
            response = client.head(SOS_RAW_DATA_URL)
            if response.status_code == 304:
                return True

            remote_checksum = hashlib.sha256(response.content).hexdigest()

        cached_path = self._get_cached_zip()
        if cached_path is None:
            return False

        cached_checksum = hashlib.sha256(cached_path.read_bytes()).hexdigest()
        return remote_checksum == cached_checksum

    def clear_cache(self) -> None:
        """Clear the cached download."""
        zip_path = self.cache_dir / "dbwebexport.zip"
        if zip_path.exists():
            zip_path.unlink()
            logger.info("Cleared cached dbwebexport.zip")
