"""MCP server entry point for the Campaign Finance Database.

Launches an MCP server with 15 read-only query tools.
Runs on port 9527 (configurable via MCP_PORT env var); serves Streamable
HTTP at /mcp and the legacy SSE transport at /sse.

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
    committees_paying_vendor,
    contributions_by_donor,
    describe_table,
    donor_watch_since,
    filing_due_soon,
    find_committees,
    get_server_docs,
    measure_spending,
    payments_to_person,
    rapid_expense_vendors,
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
    "committees_paying_vendor",
    "committee_profile",
    "find_committees",
    "measure_spending",
    "donor_watch_since",
    "upcoming_filings",
    "filing_due_soon",
    "payments_to_person",
    "rapid_expense_vendors",
    "describe_table",
    "get_server_docs",
]

# Delivered to the client in the MCP initialize response so a freshly
# attached agent gets the essentials before its first tool call.
INSTRUCTIONS = """\
California campaign-finance disclosure data (CAL-ACCESS, CA Secretary of
State), read-only. Call get_server_docs() first for the full guide;
describe_table() shows columns + gotchas for any table before ad-hoc SQL.

Essentials:
- Individuals are stored LAST-first (naml=last name, namf=first name);
  organizations sit in the naml field. Name search is word-anchored and
  case-insensitive.
- A "cycle" is derived from the transaction date; there is no year column.
- Contribution tools read the receipts_all view (periodic + 24-hour forms,
  de-duplicated) — a gift in both counts once.
- 24-hour EXPENDITURE reports (Form 496 / s496_cd) have NO payee name:
  vendor answers are a lower bound. Use rapid_expense_vendors(committee_id)
  to recover payees (80-97% resolve via date+amount match).
- Use payments_to_person(name) for "who paid X / what did X pay" questions
  — it checks payee, donor, and committee/candidate roles at once. Its
  blind_spot count shows how many unnamed 24-hour expense lines the paying
  committees have (run rapid_expense_vendors on those committees).
- Committee ids (cmte_id, e.g. C0695132) resolve to filer ids internally;
  find_committees() looks them up from names.
- Data is a snapshot (not live); check the newest transaction date for
  freshness. Snapshot: newest reports received ~2026-08-24.
"""


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
            "Read-only query tools for California campaign finance "
            "disclosure data (CAL-ACCESS): contributions, expenditures, "
            "committees, people, filing deadlines, and 24-hour-report "
            "vendor resolution."
        ),
        instructions=INSTRUCTIONS,
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
            "Get expenditures made by a committee to a vendor/payee in a cycle. "
            "The vendor is matched as a whole phrase anchored to a word "
            "boundary. Returns all matching outlay records (newest first)."
        ),
    )

    # 4. vendor_revenue
    server.add_tool(
        vendor_revenue,
        name="vendor_revenue",
        description=(
            "Get total payments made to a vendor across all committees "
            "(from expenditure records, grouped by payee). The vendor is "
            "matched as a whole phrase anchored to a word boundary, so "
            "'AL Media' hits 'AL MEDIA LLC' but not 'CENTRAL MEDIA'."
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

    # 11. committees_paying_vendor
    server.add_tool(
        committees_paying_vendor,
        name="committees_paying_vendor",
        description=(
            "Rank the committees that paid a given vendor by total amount. "
            "The vendor is matched as a whole phrase anchored to a word "
            "boundary, which catches fragmented spellings (e.g. 'AL Media' "
            "hits 'AL MEDIA LLC' but not 'CENTRAL MEDIA'). "
            "Set candidate_only=True to restrict to candidate committees "
            "(excludes ballot-measure and other committee types)."
        ),
    )

    # 12. payments_to_person
    server.add_tool(
        payments_to_person,
        name="payments_to_person",
        description=(
            "Find every role a person plays in the disclosure data in one "
            "call: payments made TO the person (as vendor/payee in "
            "expenditure records), contributions made BY the person "
            "(de-duplicated across periodic and 24-hour reports), and "
            "committees/candidates whose name matches. Name matching is "
            "field-aware and word-anchored, so 'Daly' never hits 'Odalys' "
            "and last-first storage is handled automatically. When the "
            "person was paid, the result includes a blind_spot count of "
            "the paying committees' Form 496 (24-hour) expense lines, "
            "which carry no payee name — resolve them with "
            "rapid_expense_vendors."
        ),
    )

    # 13. rapid_expense_vendors
    server.add_tool(
        rapid_expense_vendors,
        name="rapid_expense_vendors",
        description=(
            "Recover the payees of a committee's 24-hour (Form 496) "
            "expenditures, which the SOS export discloses without payee "
            "names. Matches each 24-hour line to its periodic-report "
            "re-filing by (payment date, amount) within the same filer "
            "(80-97% of lines resolve on the largest rapid-disclosure "
            "filers). Returns resolved and unresolved lines plus a "
            "resolution percentage; unresolved lines are usually recent "
            "spend awaiting the next periodic report."
        ),
    )

    # 14. describe_table
    server.add_tool(
        describe_table,
        name="describe_table",
        description=(
            "Show the columns, approximate row count, and known gotchas "
            "for any public table or view. Call this before writing "
            "ad-hoc SQL — the 24-hour tables have divergent column names "
            "(s497_cd uses amount/ctrib_date; s498_cd uses "
            "amt_rcvd/date_rcvd) and several tables carry documented "
            "join pitfalls."
        ),
    )

    # 15. get_server_docs
    server.add_tool(
        get_server_docs,
        name="get_server_docs",
        description=(
            "Return the full server quick-start guide as markdown: every "
            "tool with arguments and when to use it, the data "
            "conventions, and the known caveats. Call this first when "
            "attaching a new agent."
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

    # Dual transport on one port:
    #   - Streamable HTTP at /mcp  (current MCP spec — the only transport the
    #     Open WebUI MCP client speaks; it POSTs initialize to the connection
    #     url and would otherwise 404 and silently attach zero tools)
    #   - legacy SSE at /sse       (kept for older MCP clients like OpenCode)
    # The streamable app is the root ASGI app and owns the session-manager
    # lifespan; the SSE app is mounted into it and shares that session manager.
    import uvicorn

    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.mount("/sse", server.sse_app(sse_path="", message_path="/messages/"))

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
