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

```bash
# 1. Clone and configure (optional — compose has sane defaults)
git clone https://github.com/mdaly001/campaign-finance-tracking-db.git
cd campaign-finance-tracking-db
cp .env.example .env
# Edit .env — set DB_PASSWORD at minimum

# 2. Start PostgreSQL (schema + cfdb_reader role auto-applied on first boot)
docker compose up -d db

# 3. Load data (initial full load — downloads ~8.5 GB, may take hours).
#    Give the etl container/host ≥ 8 GB RAM (RCPT_CD is a ~3.8 GB TSV).
docker compose run --rm etl

# 4. Start the MCP server
docker compose up -d mcp

# 5. Query at http://localhost:9527/sse

# Re-check for updates (no-op if the SOS export is unchanged):
docker compose run --rm etl -- incremental \
  --database-url postgresql://cfdb:change-me@db:5432/cfdb
```

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
  - Direct download: <https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip> (~8.5 GB; the old `www.sos.ca.gov` path 404s)
  - 80 TSV files under `CalAccess/DATA/`; table docs in the `CalAccessTablesWeb.pdf`
  - Update frequency: Daily

### Federal (Planned)

- **FEC Open Data** — Federal Election Commission REST API: <https://api.open.fec.gov/>

### Prior Art Attribution

This project builds on prior analysis of California campaign finance data published by the [Los Angeles Times](https://www.latimes.com/) and other news organizations. We acknowledge their pioneering investigative work that demonstrated the public value of structured campaign finance data. This project aims to make that data more accessible for ongoing transparency research.

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
                  │  LLM     │
                  │  Client  │
                  └──────────┘
```

## License

Apache 2.0 — see [LICENSE](LICENSE) for full text.
