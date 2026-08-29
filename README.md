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

**Prefer one command?** The installer auto-detects your OS, RAM and GPU, then sets up Docker (if needed), PostgreSQL, the CAL-ACCESS data, the MCP server, a local LLM sized to your hardware, and a browser chat UI at <http://localhost:3000>. The chat arrives **pre-wired**: a default model named "Campaign Finance AI" carries all 15 cfdb tools, so users just ask questions — no MCP configuration, and nothing leaves the machine:

```bash
curl -fsSL https://raw.githubusercontent.com/mdaly001/campaign-finance-tracking-db/master/install.sh | bash
```

Useful flags: `--lite` (no local LLM), `--db-only` (just PostgreSQL + ETL + MCP — no LLM, no chat UI; ideal for a server when you already host models/agents on your network), `--llm-url URL` (use a model you already serve, e.g. `http://192.168.1.20:8080/v1`), `--no-chat`, `--no-etl` (skip the long data load for now), `--model NAME` (qwen3-14b | gpt-oss-20b | qwen3.6-35b-a3b | coder-next-80b | none), `--model-url URL` (any GGUF), `--dir PATH`, `--yes`. It is idempotent — re-run it to resume or repair. Windows users: run it inside WSL2 (the script will tell you how).

Otherwise, step by step:

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

# Re-check for updates (no-op if the SOS export is unchanged):
docker compose run --rm etl -- incremental \
  --database-url postgresql://cfdb:change-me@db:5432/cfdb
```

## Talking to the database with a local AI agent

The MCP server (port **9527**) exposes ~15 analytical tools — donor lookups,
committee profiles, payments-to-person, 24-hour-report vendor recovery, and
a built-in `get_server_docs` guide so any connected agent can self-onboard.
To use it fully offline you need two pieces:

1. **A model server** exposing an OpenAI-compatible endpoint — LM Studio,
   llama.cpp (`llama-server`), or Ollama all work.
2. **An MCP-client harness** — [OpenCode](https://opencode.ai/docs/mcp-servers/)
   (recommended; MCP is first-class), OpenClaw, Hermes Agent, or DeepSeek
   Harness. Point it at `http://localhost:9527/mcp`.

### Pick your model by RAM

The database itself needs ~3 GB (Postgres) + ~0.1 GB (MCP server). Your RAM
budget minus that is the model's. MoE models ("A3B" = ~3B active parameters)
are strongly preferred: they run fast even without a GPU.

| Your RAM | Recommended model | Quant / size | Notes |
|---|---|---|---|
| **16 GB** | **Qwen3-14B-Instruct** | Q4_K_M ≈ 9 GB | Minimum viable. Keep context ≤ 8K, enable q8 KV-cache (`--cache-type-k q8 --cache-type-v q8`), set Postgres `shared_buffers=1GB`. Tool-calling works but verify numbers on complex questions. (Apple Silicon 16 GB unified memory: gpt-oss-20b fits at MXFP4 — a better pick there.) |
| **32 GB** | **Qwen3.6-35B-A3B** (analysis) or **Qwen3-Coder-30B-A3B-Instruct** (tool-driving) | Q4_K_M ≈ 19–21 GB | The sweet spot. MoE = laptop-CPU speeds. Keep context ≤ 32K. If you want headroom instead of peak quality: **gpt-oss-20b** (≈ 13 GB) has excellent, very reliable tool calls. |
| **64 GB** | **Qwen3-Coder-Next (80B-A3B)** | Q4_K_M ≈ 45 GB | Best tool-calling + SQL available locally. Fits with Postgres (4 GB) and OS with room for a 32K context at q8 KV. Alternative: Qwen3.6-35B-A3B at higher precision / bigger context. |

Avoid sub-10B models: the tool schema, date arguments, and the data caveats
(names stored last-first, Form 496 payee blind spots, de-duplication rules)
are exactly where small models produce confidently wrong financial answers.

### Example wiring

Serve the model (llama.cpp):

```bash
llama-server -d 32768 --cache-type-k q8_0 --cache-type-v q8_0 \
  -m qwen3.6-35b-a3b-q4_k_m.gguf --port 8080
```

Connect OpenCode (`opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "cfdb": { "type": "remote", "url": "http://localhost:9527/mcp" }
  }
}
```

Then ask it things like: *"Which committees paid Inland Empire United Action
Fund since 2016, and what were their five biggest expenses?"*

**Agent tip:** your first tool call should always be `get_server_docs` — it
returns the full data-conventions guide (no repo access needed).

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
                  │  LLM     │
                  │  Client  │
                  └──────────┘
```

## License

Apache 2.0 — see [LICENSE](LICENSE) for full text.
