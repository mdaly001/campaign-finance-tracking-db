"""MCP server entry point for the Campaign Finance Database.

Launches an SSE-based MCP server with 10 Phase 1 query tools.
Runs on port 9527 (configurable via MCP_PORT env var) with /sse endpoint.

Usage:
    python -m core.mcp.server              # Start MCP server (default port)
    python -m core.mcp.server --port 8080  # Custom port
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from mcp.server import MCPServer  # type: ignore[import-untyped]

from core.etl.logging import setup_logging
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

logger = logging.getLogger(__name__)

# Tool list for introspection / tests
TOOLS: list[str] = [
    "contributions_by_donor",
    "top_donors_for_committee_or_candidate",
    "committee_outlays_to",
    "vendor_revenue",
    "committee_profile",
    "find_committees",
    "measure_spending",
    "donor_watch_since",
    "upcoming_filings",
    "filing_due_soon",
]


def _create_server() -> MCPServer:
    """Create and configure the MCP server with all tools.

    Uses the decorator-based @server.tool() API from MCP SDK v2.
    Each tool function returns a plain Python type (list, dict, str);
    the framework automatically wraps it into a CallToolResult with
    TextContent for transport.

    Returns:
        Configured MCPServer instance with tool handlers.
    """
    server = MCPServer(
        name="cfdb",
        title="Campaign Finance Database",
        description=(
            "Query tool for California campaign finance disclosure data. "
            "Provides read-only access to contributions, expenditures, "
            "committee profiles, and filing deadlines."
        ),
    )

    # 1. contributions_by_donor
    server.add_tool(
        contributions_by_donor,
        name="contributions_by_donor",
        description=(
            "Get all contributions by a donor name in a given election cycle. "
            "Supports partial name matching and alias resolution."
        ),
    )

    # 2. top_donors_for_committee_or_candidate
    server.add_tool(
        top_donors_for_committee_or_candidate,
        name="top_donors_for_committee_or_candidate",
        description=(
            "Get the top N donors to a committee or candidate in a cycle. "
            "Returns donors sorted by total contribution amount."
        ),
    )

    # 3. committee_outlays_to
    server.add_tool(
        committee_outlays_to,
        name="committee_outlays_to",
        description=(
            "Get expenditures made by a committee to vendors/payees in a cycle. "
            "Returns all outlay records from the committee."
        ),
    )

    # 4. vendor_revenue
    server.add_tool(
        vendor_revenue,
        name="vendor_revenue",
        description=(
            "Get total revenue received by a vendor name across committees. "
            "Aggregates receipts and expenditures for the vendor."
        ),
    )

    # 5. committee_profile
    server.add_tool(
        committee_profile,
        name="committee_profile",
        description=(
            "Get a summary profile of a committee including name, type, "
            "location, and financial summary (receipts, disbursements, cash)."
        ),
    )

    # 6. find_committees
    server.add_tool(
        find_committees,
        name="find_committees",
        description=(
            "Find committees by (partial) name and return their IDs. "
            "Use the returned cmte_id as committee_id in the other tools."
        ),
    )

    # 7. measure_spending
    server.add_tool(
        measure_spending,
        name="measure_spending",
        description=(
            "Get spending totals for a ballot measure. "
            "Searches campaign disclosure records for the measure."
        ),
    )

    # 8. donor_watch_since
    server.add_tool(
        donor_watch_since,
        name="donor_watch_since",
        description=(
            "Get contributions from a donor since a given date. "
            "Useful for monitoring new donor activity. Optional name filter."
        ),
    )

    # 9. upcoming_filings
    server.add_tool(
        upcoming_filings,
        name="upcoming_filings",
        description=(
            "Get upcoming filing deadlines within a date range. "
            "Queries the filing calendar for pending deadlines."
        ),
    )

    # 10. filing_due_soon
    server.add_tool(
        filing_due_soon,
        name="filing_due_soon",
        description=(
            "Get all filings due within the next N days. "
            "Defaults to 7 days. Reports OPEN status filings."
        ),
    )

    for tool_name in TOOLS:
        logger.info("Registered MCP tool: %s", tool_name)

    return server


def main() -> None:
    """Entry point: start the MCP SSE server."""
    parser = argparse.ArgumentParser(description="Campaign Finance DB MCP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "9527")),
        help="Port to listen on (default: 9527)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Override DATABASE_URL from environment",
    )
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    logger.info("Starting MCP server on %s:%d", args.host, args.port)
    logger.info("Database URL: %s", os.environ.get("DATABASE_URL", "(from env)"))

    server = _create_server()

    try:
        asyncio.run(
            server.run_sse_async(
                host=args.host,
                port=args.port,
                sse_path="/sse",
                message_path="/messages/",
            )
        )
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
