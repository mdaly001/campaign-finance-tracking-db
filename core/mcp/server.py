"""MCP server entry point for the Campaign Finance Database.

Launches an MCP server with 20 read-only query tools (19 domain tools + a
run_sql escape hatch for edge-case queries).
Runs on port 9527 (configurable via MCP_PORT env var); serves the Streamable
HTTP transport at /mcp (point MCP clients at http://<host>:9527/mcp).

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
from core.mcp.entity_tools import (
    entity_contributions,
    entity_expenditures,
    returned_contributions,
)
from core.mcp.federal_tools import (
    fec_find_candidate,
    fec_ie_by_org,
    fec_ie_summary,
    fec_race_overview,
    fec_spender_breakdown,
    fec_top_spending,
)
from core.mcp.tools import (
    committee_outlays_to,
    committee_profile,
    committees_paying_vendor,
    contributions_by_donor,
    data_freshness,
    describe_table,
    donor_watch_since,
    filing_due_soon,
    find_committees,
    fuzzy_name_search,
    get_server_docs,
    measure_spending,
    payments_to_person,
    rapid_expense_vendors,
    refunds_to_donors,
    run_sql,
    top_donors_for_committee_or_candidate,
    total_expenditures,
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
    "fuzzy_name_search",
    "total_expenditures",
    "refunds_to_donors",
    "data_freshness",
    "describe_table",
    "run_sql",
    "get_server_docs",
    # Federal (FEC) independent-expenditure tools
    "fec_find_candidate",
    "fec_ie_summary",
    "fec_top_spending",
    "fec_spender_breakdown",
    "fec_race_overview",
    "fec_ie_by_org",
    # Cross-source (state + federal) entity money tools
    "entity_contributions",
    "entity_expenditures",
    "returned_contributions",
]

# Delivered to the client in the MCP initialize response so a freshly
# attached agent gets the essentials before its first tool call.
INSTRUCTIONS = """\
California campaign-finance disclosure data (CAL-ACCESS, CA Secretary of
State), read-only. Call get_server_docs() first for the full guide;
describe_table() shows columns + gotchas for any table before ad-hoc SQL;
run_sql(sql) runs a single read-only SELECT/WITH/EXPLAIN — the escape hatch
for edge-case questions no dedicated tool covers.

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
- Data is a snapshot (not live): call data_freshness() before quoting
  "current" totals — it reports the newest receipt/expenditure dates,
  the last ETL load, and how many future-dated corrupt rows it excludes
  from the freshness figure (the caveats' data-hygiene quirk).

FEDERAL (FEC) — candidate-targeted independent expenditures (Schedule E):
- Federal IE tools are prefixed fec_. They read the de-duplicated,
  name-resolved view fec.fec_ie_targeted_resolved, which collapses the F24
  48-hour-notice / F3X periodic-report double-count AND cross-filing
  re-filings, and folds NULL-id records into their named candidate. NEVER sum
  the raw fec.fec_ie_targeted table for a total.
- fec_find_candidate(name, state, district, cycle) resolves a name to a FEC
  candidate_id (substring OR fuzzy trigram; each hit has a `match` score).
- fec_ie_summary(candidate_id, cycle, start_date, end_date) gives
  for/against totals (start inclusive, end exclusive).
- fec_top_spending(candidate_id, side='against'|'for', cycle, limit) ranks
  spenders; fec_spender_breakdown(spender_id, ...) shows what they bought
  (payee / purpose / target); fec_race_overview(state, district, cycle)
  gives the whole for/against picture across a race.
- fec_ie_by_org(state, district, cycle, election_type, min_amount) lists
  every (org -> candidate -> side) combination above a threshold, one row
  each — so a committee that spent for one candidate and against another
  shows as two rows, with a human `purpose` string.
- cycle is the FEC two-year PERIOD (e.g. 2026); it does NOT pick the
  election. Pass election_type='primary'|'general'|'special'|'runoff'|
  'convention'|'other' (or a P/G/R/S/C/O code) to isolate one election
  within the cycle. Omit cycle to span all loaded cycles.

CROSS-SOURCE (state + federal) — one entity, both registries:
- entity_contributions(entity, year, min_amount) = who gave TO an entity,
  and entity_expenditures(entity, year, min_amount) = what it spent, each
  looking at BOTH the California (CAL-ACCESS) and federal (FEC) systems.
  The entity is resolved by NAME across both registries (it may be filed in
  one, the other, or both). Use these when you don't know where an org files,
  or want its whole money picture in one call. `total`/`by_source`/`by_year`
  are full totals; the contributors/expenditures lists are the top-N view.
- returned_contributions(entity, year, min_amount) = the money the entity gave
  BACK to donors. Refunds live OUTSIDE the contribution ledger (state: Schedule
  E refund/return expenditures; federal: negative contributions), so this is
  how you reconcile what a committee actually kept vs. what it received.
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
            "Read-only query tools for campaign finance disclosure data: "
            "California (CAL-ACCESS) contributions, expenditures, committees, "
            "people, filing deadlines and 24-hour-report vendor resolution, "
            "PLUS federal (FEC) candidate-targeted independent expenditures "
            "(Schedule E) — for/against totals, top spenders, and "
            "spender/race breakdowns, all de-duplicated."
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

    # 16. run_sql — SQL escape hatch for edge cases no dedicated tool covers
    server.add_tool(
        run_sql,
        name="run_sql",
        description=(
            "Run ONE read-only SQL query (must start with SELECT, WITH, or "
            "EXPLAIN) against the campaign-finance database. Prefer a dedicated "
            "tool when one fits; use this for edge-case/ad-hoc questions. "
            "Guards: single statement, 15s timeout, capped rows; runs as a "
            "read-only role so writes are impossible."
        ),
    )

    # 17. total_expenditures — dedup-safe total spend for a committee/cycle
    server.add_tool(
        total_expenditures,
        name="total_expenditures",
        description=(
            "Total expenditures of a committee in an election cycle, read "
            "from the amendment-deduplicated expenditure view (Form E + "
            "F461P5 only; Form D/G tracking copies excluded so nothing "
            "double-counts). Scoped by filing ownership through "
            "filer_filings_cd — not the detail-line cmte_id. Set "
            "exclude_refunds=True to drop 'return of contribution' refund "
            "lines and see pure vendor spend."
        ),
    )

    # 18. refunds_to_donors — refund lines separated from vendor spend
    server.add_tool(
        refunds_to_donors,
        name="refunds_to_donors",
        description=(
            "Refund lines ('return of contribution' style memo codes) filed "
            "by a committee in a cycle, grouped by the payee that received "
            "the refund (the original donor or their agent). Refunds live in "
            "the expenditure tables and inflate vendor-spend totals if not "
            "separated out."
        ),
    )

    # 19. data_freshness — snapshot freshness + data-hygiene anomaly counts
    server.add_tool(
        data_freshness,
        name="data_freshness",
        description=(
            "Snapshot freshness report: newest receipt and expenditure dates "
            "(capped at today — corrupt future-dated rows are excluded and "
            "counted separately as a data-hygiene signal), the last ETL load "
            "timestamp, and approximate row counts for the core fact "
            "tables. Call this before quoting 'current' totals so answers "
            "can state how current the snapshot actually is."
        ),
    )

    server.add_tool(
        fuzzy_name_search,
        name="fuzzy_name_search",
        description=(
            "Resolve-first lookup of a person or organization: every "
            "transaction a name touched, grouped by canonical entity. "
            "Word-anchored, order-insensitive matching across every stored "
            "name variant ('Michael Daly' == 'DALY, MICHAEL' == "
            "'Daly M. Michael'); transactions are deduplicated per "
            "(cmte_id, tran_id) and totals are grouped by year and entity. "
            "Call this FIRST for 'who paid whom' / 'what did X pay or get' "
            "questions — the answer is complete on the first call, no "
            "variant guessing. entity_type: all|donor|payee|committee|"
            "candidate."
        ),
    )

    # ---- Federal (FEC) independent-expenditure tools ---- #
    server.add_tool(
        fec_find_candidate,
        name="fec_find_candidate",
        description=(
            "Resolve a federal candidate name to FEC candidate id(s) from "
            "Schedule E records. Optional state/district/cycle filters. Use "
            "the returned candidate_id in the other fec_* tools."
        ),
    )
    server.add_tool(
        fec_ie_summary,
        name="fec_ie_summary",
        description=(
            "De-duplicated for/against independent-expenditure totals for a "
            "federal candidate. Optional date window (start inclusive, end "
            "exclusive, ISO YYYY-MM-DD) and cycle. Answers 'how much was "
            "spent for and against candidate X' without double-counting."
        ),
    )
    server.add_tool(
        fec_top_spending,
        name="fec_top_spending",
        description=(
            "Top N committees spending for or against a federal candidate "
            "(de-duplicated). side='against' (default) or 'for'."
        ),
    )
    server.add_tool(
        fec_spender_breakdown,
        name="fec_spender_breakdown",
        description=(
            "What a given federal spending committee bought, de-duplicated: "
            "by payee, by purpose (ad buy vs production vs other), and by "
            "target candidate. Optionally scope to one candidate/cycle."
        ),
    )
    server.add_tool(
        fec_race_overview,
        name="fec_race_overview",
        description=(
            "All federal independent expenditures in a state/district race, "
            "grouped by candidate and for/against side (de-duplicated). "
            "Gives the whole race's for/against picture at once."
        ),
    )
    server.add_tool(
        fec_ie_by_org,
        name="fec_ie_by_org",
        description=(
            "Every (org -> candidate -> side) independent-expenditure "
            "combination above a threshold in a race, one row each. Shows how "
            "much a committee spent and whether it was in support of or in "
            "opposition to a specific candidate. A committee that spent for one "
            "candidate and against another appears as two separate rows. "
            "De-duplicated and name-resolved. Filter by election phase "
            "(primary/general/special) and min_amount."
        ),
    )
    server.add_tool(
        entity_contributions,
        name="entity_contributions",
        description=(
            "Who gave TO an entity (money IN), across BOTH California "
            "(CAL-ACCESS) and federal (FEC) filings. Resolves the entity by "
            "name in both registries (it may exist in one, the other, or both) "
            "and returns full totals plus the top contributors. Optional year "
            "(calendar year, or all) and min_amount (only contributors whose "
            "aggregated total meets it). Use to see an org's funding base in "
            "one call regardless of where it files."
        ),
    )
    server.add_tool(
        entity_expenditures,
        name="entity_expenditures",
        description=(
            "What an entity spent (money OUT), across BOTH California "
            "(CAL-ACCESS) and federal (FEC) filings. Resolves the entity by "
            "name in both registries and returns full totals plus the top "
            "spending targets (federal IEs by candidate+side; state by "
            "payee/purpose). Optional year and min_amount. Federal 2026 is the "
            "de-duplicated targeted view; 2024 is the full load deduped by "
            "transaction_id (candidate targeting not captured in that load)."
        ),
    )
    server.add_tool(
        returned_contributions,
        name="returned_contributions",
        description=(
            "Contributions an entity RETURNED / REFUNDED to donors, across BOTH "
            "California and federal filings. A refund is not on the contribution "
            "side: state refunds are Schedule E expenditures whose purpose is a "
            "contribution refund/return (recipient = payee); federal refunds are "
            "negative contributions (reported as a positive refund magnitude). "
            "Use to reconcile what a committee actually kept vs. received, and "
            "to find committees that return a lot of money. Optional year and "
            "min_amount (threshold on the refund to each recipient)."
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

    # Serve the Streamable HTTP transport (current MCP spec) — the only transport
    # Streamable-HTTP MCP clients speak (they POST the initialize handshake to the
    # connection url; on an SSE-only server they get a 404 and silently attach
    # zero tools, so the agent hallucinates instead of querying the database).
    # The SDK's own runner is used (no uvicorn import here — uvicorn is not a
    # hard dependency of this image; importing it at runtime would crash-loop
    # the container). Legacy SSE consumers must point at /mcp now.
    asyncio.run(
        server.run_streamable_http_async(
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
        )
    )


if __name__ == "__main__":
    main()
