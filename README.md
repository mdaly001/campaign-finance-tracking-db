# Campaign Finance Disclosure Database

A privacy-first campaign finance database for California, with a path to federal coverage. Download publicly available disclosure data from the California Secretary of State's [CAL-ACCESS](https://calaccess.calstate.edu/) system, load it into PostgreSQL, and query it through a standards-compliant MCP (Model Context Protocol) server.

## Project Description

This project ingests and normalizes campaign finance disclosure data from California state elections (and later federal elections) into a local PostgreSQL database. An MCP server exposes analytical tools that can be queried by any LLM client, enabling transparent, auditable campaign finance analysis without relying on third-party APIs.

**Privacy-first design:** Redacted data is loaded by default. Unredacted PII is opt-in and isolated in a separate schema with guardrails to prevent accidental exposure.

## Setup

### Prerequisites

- Docker & Docker Compose (v2)
- `uv` for local development (`pip install uv`)

### Quick Start

Step by step:

```bash
# 1. Clone and configure (optional — compose has sane defaults)
git clone https://github.com/mdaly001/campaign-finance-tracking-db.git
cd campaign-finance-tracking-db
cp .env.example .env
# Edit .env — set DB_PASSWORD at minimum

# 2. Start PostgreSQL (schema + cfdb_reader role auto-applied on first boot)
docker compose up -d db

# 3. Load data (initial full load — downloads ~1.5 GB, may take hours).
#    Give the etl container/host ≥ 8 GB RAM (RCPT_CD is a ~3.8 GB TSV).
docker compose run --rm etl

# 4. Start the MCP server
docker compose up -d mcp

# 5. Query at http://localhost:9527/mcp

# Re-check for updates (no-op unless the SOS export changed);
# substitute the DB_PASSWORD you set in .env:
docker compose run --rm etl -- incremental \
  --database-url postgresql://cfdb:YOUR_DB_PASSWORD@db:5432/cfdb
```

## Querying the database with an MCP client or AI agent

The MCP server (port **9527**) exposes ~15 analytical tools — donor lookups,
committee profiles, payments-to-person, 24-hour-report vendor recovery, and
a built-in `get_server_docs` guide so any connected agent can self-onboard.
The server is **read-only**: it connects to Postgres as the `cfdb_reader`
role, so connected clients can query but never write.

Any MCP-capable client can connect — [OpenCode](https://opencode.ai/docs/mcp-servers/)
(MCP is first-class), Claude Desktop, OpenClaw, Hermes Agent, or any
Streamable-HTTP MCP client. Point it at `http://localhost:9527/mcp`
(from a container on the same host use `http://host.docker.internal:9527/mcp`).

### Example wiring

Point your MCP client at the server (OpenCode `opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "cfdb": { "type": "remote", "url": "http://localhost:9527/mcp" }
  }
}
```

Then ask it things like: *"Which committees paid VENDOR1 since 2016, and what were their five biggest expenses?"*

**Agent tip:** your first tool call should always be `get_server_docs` — it
returns the full data-conventions guide (no repo access needed).

### Instructing your agent/harness (CRITICAL)

When connecting an LLM to this database, you **must ensure the agent reads
the documentation files** before writing queries. Without this context, the
agent will produce confidently wrong financial answers due to dataset quirks
that are invisible from the schema alone.

**Three files every agent MUST read:**

1. **`docs/data_caveats.md`** — All the gotchas that silently distort results:
   name storage order, 24-hour report dedup, row-count inflation from joins,
   committee renaming, refund-as-expenditure, and the correct SQL templates
   for common queries.

2. **`docs/mcp_server.md`** — The MCP tool catalog and data conventions.
   Call `get_server_docs` first if the agent doesn't have repo access — it
   returns this exact text.

3. **`docs/data_dictionary.md`** — Full table/column definitions.

**How to inject these into an agent:**

- **CLI / GUI harnesses (Hermes, OpenCode, etc.):** Put the docs in your session
  as context or a `.agents.md` / `AGENTS.md` file in the repo root. Most
  harnesses auto-inject these on session start.

- **Programmatic:** Include the docs as system messages in your API calls
  before any tool-invocation turns.

**Recommended minimum system prompt for any agent connected to the MCP server:**

```
You are a California campaign-finance analyst. For ANY factual question
about committees, candidates, donors, contributions, expenditures, vendors,
or ballot measures, you MUST call the attached CAL-ACCESS tools (cfdb_*)
and answer only from their results — never from memory.

CRITICAL RULES:
- Always call `get_server_docs` first to load data conventions.
- Names are stored LAST-FIRST (e.g. payee_naml='Daly', namf='Michael Gomez').
- On rcpt_cd, cmte_id identifies the DONOR's committee, not the recipient.
  Always scope queries through filer_filings_cd, not cmte_id.
- 24-hour expenditure reports (s496_cd) have NO payee names — only a
  description. Vendor data from expn_cd is a lower bound; use
  rapid_expense_vendors to recover payees.
- Never trust a large total without spot-checking the raw rows for that
  filing_id — a filing may contain many different donors, not just the one
  you're interested in.
- If a tool returns nothing, say so plainly instead of guessing.
```

> Note: MCP support varies by harness version. OpenCode supports MCP servers
> natively; OpenClaw and Hermes Agent's support is adapter/skill-based —
> check their current docs before assuming the 15 tools will appear.

### Local Development

```bash
uv sync
uv run ruff check .
uv run pytest tests/
```

## Data Sources

### California State (Primary)

- **SOS CAL-ACCESS Database** — Daily TSV dump from the California Secretary of State's Campaign Disclosure Data system (docs: <https://calaccess.calstate.edu/>)
  - Dataset: `dbwebexport.zip` (all CA campaign finance, lobbying, ballot measure, and election result data)
  - Direct download: <https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip> (~1.5 GB; the old `www.sos.ca.gov` path 404s)
  - 80 TSV files under `CalAccess/DATA/`; table docs in the `CalAccessTablesWeb.pdf`
  - Update frequency: Daily

### Federal (Planned)

- **FEC Open Data** — Federal Election Commission REST API: <https://api.open.fec.gov/>

### Prior Art Attribution

This project builds on prior analysis of California campaign finance data published by the Los Angeles Times and other news organizations. We acknowledge their pioneering investigative work that demonstrated the public value of structured campaign finance data. This project aims to make that data more accessible for ongoing transparency research.

## Privacy Notice

This project prioritizes privacy and responsible data handling:

- **Default mode loads redacted data only.** The redacted CAL-ACCESS dataset is public information.
- **Unredacted data is opt-in.** Loading unredacted PII requires setting `UNREDACTED_ENABLED=true` in `.env` and manually placing data into a designated volume.
- **A separate `unredacted` schema** with a read-only `cfdb_reader` role that cannot access unredacted data.
- **All raw data is stored locally.** No data is transmitted to third-party servers.
- **Users are responsible** for compliance with applicable privacy laws when using unredacted data.

## Phases

### Phase 1 — California State
Ingest CAL-ACCESS data (committee, candidate, donor, contribution, expenditure, loan, transfer, lobbying, ballot measure). Full historical load plus daily incremental updates.

### Phase 2 — Federal
Ingest FEC campaign finance data. Cross-reference with California data for multi-jurisdiction entities.

### Phase 3 — California Counties
Ingest county-level disclosure data for comprehensive local coverage.

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│   ETL    │────▶│  POSTGRES│◀───▶│   MCP    │
│  (daily) │     │   (16)   │     │  Server  │
└──────────┘     └──────────┘     └──────────┘
                       │
                  ┌──────────┐
                  │   MCP    │
                  │ clients  │
                  └──────────┘
```

## License

Apache 2.0 — see [LICENSE](LICENSE) for full text.
