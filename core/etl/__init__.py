"""Campaign finance ETL infrastructure.

Public exports:
    SourceAdapter    — abstract interface for data sources
    SourceFileInfo   — metadata about a source file
    LoadSummary      — load operation summary
    TSVReader        — tab-delimited parser
    LoadCheckpoint   — idempotent load tracking
    upsert_records   — generic PostgreSQL upsert
    DeadLetter       — bad-row quarantine
    setup_logging    — JSON structured logging
"""

from core.etl.adapter import LoadSummary, SourceAdapter, SourceFileInfo
from core.etl.checkpoint import LoadCheckpoint
from core.etl.dead_letter import DeadLetter
from core.etl.logging import setup_logging
from core.etl.tsv import TSVReader
from core.etl.upsert import upsert_records

__all__ = [
    "LoadCheckpoint",
    "LoadSummary",
    "DeadLetter",
    "SourceAdapter",
    "SourceFileInfo",
    "TSVReader",
    "upsert_records",
    "setup_logging",
]
