"""SourceAdapter abstract interface for campaign finance data ingestion."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass
class SourceFileInfo:
    """Metadata about a source data file.

    Attributes:
        name: Short filename (e.g. "CandNomv18_22.tsv")
        url: Download URL for the file
        checksum: Expected SHA-256 hex digest (None if unknown)
        size: File size in bytes (optional, set after download)
    """

    name: str
    url: str
    checksum: str | None = None
    size: int = 0


@dataclass
class LoadSummary:
    """Summary of a single load operation.

    Attributes:
        rows_read: Total rows parsed from the source
        rows_upserted: Rows successfully inserted or updated
        rows_skipped: Rows skipped (e.g. via checkpoint)
        rows_failed: Rows that raised errors (sent to dead-letter)
        duration_seconds: Elapsed wall-clock time (optional)
    """

    rows_read: int = 0
    rows_upserted: int = 0
    rows_skipped: int = 0
    rows_failed: int = 0
    duration_seconds: float = 0.0


class SourceAdapter(ABC):
    """Abstract interface for a data source.

    Implementations cover "state" (CAL-ACCESS), "federal", and "local"
    campaign finance data sources.

    Attributes:
        source: One of "state", "federal", or "local"
    """

    source: str  # "state", "federal", or "local"

    @abstractmethod
    def get_source_files(self) -> list[SourceFileInfo]:
        """Return metadata for all files available from this source."""
        ...

    @abstractmethod
    def fetch_file(self, info: SourceFileInfo) -> bytes:
        """Download a single file and return its raw bytes."""
        ...

    @abstractmethod
    def parse_file(self, raw: bytes) -> Iterator[dict[str, Any]]:
        """Yield record dicts from raw file bytes.

        Implementations may use TSVReader or a CSV parser depending
        on the source format.
        """
        ...

    @abstractmethod
    def upsert_records(
        self, records: Iterator[dict[str, Any]], session
    ) -> LoadSummary:
        """Insert or update records into the database.

        Args:
            records: Iterator of record dicts
            session: SQLAlchemy session
        """
        ...

    @abstractmethod
    def compute_checksum(self, raw: bytes) -> str:
        """Return hex digest of the raw bytes (SHA-256)."""
        ...
