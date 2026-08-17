"""Workflows package: scheduler, filing calendar, entity resolution hooks.

Exposes:
    - scheduler: Daily incremental load runner
    - filing_calendar: Filing deadline computation
    - entity_resolution: Donor/vendor alias matching (future)
"""

from __future__ import annotations

from core.workflows.scheduler import run_scheduler

__all__ = ["run_scheduler"]
