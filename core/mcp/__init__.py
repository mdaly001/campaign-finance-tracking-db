"""MCP server module for the Campaign Finance Database.

Provides read-only query tools over the CAL-ACCESS database
via the Model Context Protocol (MCP).
"""

from core.mcp.db import execute_read, get_engine
from core.mcp.server import main
from core.mcp.tools import (
    committee_outlays_to,
    committee_profile,
    contributions_by_donor,
    donor_watch_since,
    filing_due_soon,
    find_committees,
    measure_spending,
    top_donors_for_committee_or_candidate,
    upcoming_filings,
    vendor_revenue,
)

__all__ = [
    # DB helpers
    "get_engine",
    "execute_read",
    # Server
    "main",
    # Tools (Phase 1: 10 tools)
    "contributions_by_donor",
    "top_donors_for_committee_or_candidate",
    "committee_outlays_to",
    "vendor_revenue",
    "committee_profile",
    "measure_spending",
    "donor_watch_since",
    "upcoming_filings",
    "filing_due_soon",
]
