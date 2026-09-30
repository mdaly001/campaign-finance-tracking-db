"""FECSourceAdapter for FEC federal campaign finance data ingestion.

Downloads bulk files from fec.gov/files/bulk-downloads/<cycle>/,
handles zip extraction, checksums, and incremental delta detection.

Implements the SourceAdapter ABC from core.etl.adapter.
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

# Configuration
FEC_BULK_BASE_URL = os.getenv(
    "FEC_BULK_BASE_URL",
    "https://www.fec.gov/files/bulk-downloads",
)
FEC_CACHE_DIR = Path(os.getenv("FEC_CACHE_DIR", "/app/federal/cache"))


@dataclass
class FECSourceInfo:
    """Metadata about an FEC source file."""

    cycle: int
    file_type: str
    name: str
    url: str
    zip_member: str | None = None
    checksum: str | None = None
    size: int = 0


class FECSourceAdapter(SourceAdapter):
    """Download and parse FEC federal campaign finance data.

    Downloads bulk files from fec.gov/files/bulk-downloads/<cycle>/,
    extracts zip members, computes checksums for incremental detection.
    """

    source = "federal"

    def __init__(
        self,
        cache_dir: Path | None = None,
        refresh: bool = False,
    ) -> None:
        """
        Args:
            cache_dir: Directory for cached FEC files.
            refresh: If True, always re-download (ignore cache).
        """
        self.cache_dir = cache_dir or FEC_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.reader = TSVReader(
            has_header=False,
            empty_to_none=True,
            delimiter="|",
        )
        self.force_refresh = refresh
        self._file_checksums: dict[str, str] = {}

    def get_source_files(self) -> list[SourceFileInfo]:
        """Return metadata for all FEC files available for configured cycles."""
        cycles = self._get_cycles()
        files: list[SourceFileInfo] = []

        for cycle in cycles:
            cycle_dir = Path(f"{FEC_BULK_BASE_URL}/{cycle}")
            files.extend(self._get_cycle_files(cycle, cycle_dir))

        logger.info("Found %d FEC source files across cycles %s", len(files), cycles)
        return sorted(files, key=lambda f: (f.url, f.name))

    def get_deltas(self, cycle: int) -> dict[str, SourceFileInfo]:
        """Return insert/delete delta files for a cycle."""
        cycle_dir = Path(f"{FEC_BULK_BASE_URL}/{cycle}")
        deltas: dict[str, SourceFileInfo] = {}

        # Insert delta
        insert_url = cycle_dir / f"indiv{cycle % 100}_insert.zip"
        if self._url_exists(str(insert_url)):
            deltas["insert"] = SourceFileInfo(
                name=f"indiv{cycle % 100}_insert.zip",
                url=str(insert_url),
                zip_member="insert_itcont.txt",
            )

        # Delete delta
        delete_url = cycle_dir / f"indiv{cycle % 100}_delete.zip"
        if self._url_exists(str(delete_url)):
            deltas["delete"] = SourceFileInfo(
                name=f"indiv{cycle % 100}_delete.zip",
                url=str(delete_url),
                zip_member="delete_itcont.txt",
            )

        return deltas

    def fetch_file(self, info: SourceFileInfo) -> bytes:
        """Download a single FEC file and return raw bytes."""
        import zipfile
        import io

        cache_path = self.cache_dir / info.name

        # Check cache
        if not self.force_refresh and cache_path.exists():
            logger.info("Using cached file: %s", info.name)
            zip_bytes = cache_path.read_bytes()
        else:
            # Download
            logger.info("Downloading: %s", info.url)
            with httpx.Client(timeout=300.0, follow_redirects=True) as client:
                response = client.get(info.url)
                response.raise_for_status()

                zip_bytes = response.content
                cache_path.write_bytes(zip_bytes)
                logger.info("Downloaded and cached: %s (%d bytes)", info.name, len(zip_bytes))

        # Extract zip member if specified
        if info.zip_member:
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    # Find the actual member name (might have path prefix)
                    members = zf.namelist()
                    member_name = None
                    for m in members:
                        if m == info.zip_member or m.endswith(f"/{info.zip_member}"):
                            member_name = m
                            break
                    if member_name is None:
                        # Try adding .txt extension
                        for m in members:
                            if m == info.zip_member + ".txt" or m.endswith(f"/{info.zip_member}.txt"):
                                member_name = m
                                break
                    if member_name is None:
                        logger.warning("Zip member not found: %s in %s (available: %s)",
                                       info.zip_member, info.name, members)
                        return zip_bytes
                    return zf.read(member_name)
            except zipfile.BadZipFile as e:
                logger.warning("Bad zip file %s: %s", info.name, e)
                return zip_bytes

        return zip_bytes

    def parse_file(self, raw: bytes) -> Iterator[dict[str, Any]]:
        """Parse raw FEC file bytes into an iterator of dicts.

        FEC files are pipe-delimited with no header. Column names are
        determined by the FEC_TABLE_DEFINITIONS registry.
        """
        return iter(self.reader.read_bytes(raw))

    def upsert_records(
        self,
        records: Iterator[dict[str, Any]],
        session: Any,
    ) -> LoadSummary:
        """Upsert parsed FEC records into the database."""
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
        """Stable checksum of the current source (not applicable for FEC)."""
        return None

    def is_up_to_date(self) -> bool:
        """FEC files don't support is_up_to_date; use per-file checksums."""
        raise NotImplementedError("Use per-file checksums for FEC")

    def refresh(self) -> None:
        """Force fresh download on next access."""
        self.force_refresh = True

    # -- Internal helpers --

    def _get_cycles(self) -> list[int]:
        """Get configured FEC cycles."""
        cycles_env = os.getenv("FEC_CYCLES", "2024,2026")
        try:
            return [int(c.strip()) for c in cycles_env.split(",") if c.strip()]
        except ValueError:
            logger.warning("Invalid FEC_CYCLES config: %s", cycles_env)
            return [2024, 2026]

    def _get_cycle_files(self, cycle: int, cycle_dir: Path) -> list[SourceFileInfo]:
        """Get all source files for a cycle."""
        files: list[SourceFileInfo] = []

        # Committee master
        files.append(self._make_file_info(
            cycle, "cm", f"cm{cycle % 100}.zip", "cm.txt", cycle_dir
        ))

        # Candidate master
        files.append(self._make_file_info(
            cycle, "cn", f"cn{cycle % 100}.zip", "cn.txt", cycle_dir
        ))

        # Candidate-committee linkage
        files.append(self._make_file_info(
            cycle, "ccl", f"ccl{cycle % 100}.zip", "ccl.txt", cycle_dir
        ))

        # Individual contributions
        files.append(self._make_file_info(
            cycle, "indiv", f"indiv{cycle % 100}.zip", "itcont", cycle_dir
        ))

        # Other contributions
        files.append(self._make_file_info(
            cycle, "oth", f"oth{cycle % 100}.zip", "itoth", cycle_dir
        ))

        # Conduit contributions
        files.append(self._make_file_info(
            cycle, "pas2", f"pas2{cycle % 100}.zip", "itpas2", cycle_dir
        ))

        # Independent expenditures
        files.append(self._make_file_info(
            cycle, "oppexp", f"oppexp{cycle % 100}.zip", "oppexp", cycle_dir
        ))

        return [f for f in files if f.url]

    def _make_file_info(
        self,
        cycle: int,
        file_type: str,
        filename: str,
        member: str,
        cycle_dir: Path,
    ) -> SourceFileInfo:
        """Create SourceFileInfo for an FEC file."""
        # FEC URLs are: https://www.fec.gov/files/bulk-downloads/YYYY/filename.zip
        url = f"{FEC_BULK_BASE_URL}/{cycle}/{filename}"
        if not self._url_exists(url):
            logger.warning("FEC file not found: %s", url)
            return SourceFileInfo(
                name=filename, url=url, zip_member=member, size=0
            )

        return SourceFileInfo(
            name=filename,
            url=url,
            zip_member=member,
        )

    def _url_exists(self, url: str) -> bool:
        """Check if a URL exists (HEAD request)."""
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.head(url)
                return response.status_code < 400
        except httpx.HTTPError:
            return False
