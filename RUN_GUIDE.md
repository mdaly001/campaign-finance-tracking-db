# Running the Campaign Finance DB — Plain-English Guide

This folder contains two things:

1. **A database** of California campaign-finance and lobbying disclosure data
   (the official State of California SOS "CAL-ACCESS" export — ~80.7 million rows
   across 80 tables: contributions, expenditures, committees, filers, lobbying, etc.)
2. **A query server** (MCP) that lets AI apps and other programs ask the
   database questions ("top donors to committee X in 2024", "spending on
   Measure Y") over a standard protocol.

You run the whole thing with **Docker**. This guide assumes Docker and
Docker Compose are installed (`docker compose version` should print a version).

## What you need from the machine

| Requirement | Minimum | Recommended |
|---|---|---|
| Free disk | ~30 GB | ~50 GB |
| RAM | 8 GB | 16 GB |
| One-time download | ~1.5 GB (the SOS data zip) | |
| First load time | a few hours | |

The disk is for the data zip (~1.5 GB) plus the loaded database (~17-20 GB).
RAM matters because one big file (contributions, 3.8 GB) is parsed in memory.

## The four steps

### 1. Get the code onto the machine

Either `git clone` this repo, or copy the whole folder. Everything you need
is in the folder itself (data is downloaded from the State of California on
first run — nothing else to fetch).

### 2. Start the database

```bash
docker compose up -d db
```

This starts PostgreSQL 16 in a container named `cfdb-db`. On its **first**
boot it automatically creates the database `cfdb` with all 88 tables.
Data survives restarts in a Docker volume (`pgdata`).

Check it's healthy:

```bash
docker compose exec db pg_isready -U cfdb
```

### 3. Load the data (the long step)

```bash
docker compose run --rm etl
```

What it does, in order:

1. Downloads the official SOS export zip from
   `https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip` (~1.5 GB,
   one time — it's cached in the `statecache` volume afterwards).
2. Loads all 80 tables into Postgres in dependency order.
3. Keeps a checkpoint per table, so **if it gets interrupted you can just
   re-run the same command** and it picks up where it left off.

Expect **2-4 hours** the first time (most of it is the two 3 GB files:
contributions and expenditures). Watch progress:

```bash
docker compose logs -f etl
```

You'll see lines like `Loaded 1000 rows into rcpt_cd (progress: 12000000)`
and, at the end of each table, a summary like
`Table RCPT_CD: 20180537 read, 20180537 upserted, 0 skipped, 0 failed`.

A small number of rows (~1,000 out of 80.7 million) are broken **in the
State's own source files** — the loader logs them into an audit table called
`etl_dead_letter` instead of failing. That is expected and fine.

### 4. Start the query server

```bash
docker compose up -d mcp
```

This starts the MCP (Model Context Protocol) server on port **9527**.
It is **read-only** — it connects with a separate `cfdb_reader` database
user that cannot modify data.

## Checking it works

Three quick checks, in order:

**a. The database has data** (expect ~20.18 million):

```bash
docker compose exec db psql -U cfdb -d cfdb -c "SELECT COUNT(*) FROM rcpt_cd;"
```

**b. The query server answers** (expect an `event: endpoint` line):

```bash
curl -N http://localhost:9527/sse &
sleep 2; kill %1
```

**c. (Optional) connect a client.** Point any MCP client at
`http://localhost:9527/sse`. It exposes 9 tools, for example:

- `contributions_by_donor` — everything a donor has given, with cycle filter
- `top_donors_for_committee_or_candidate` — biggest donors to a committee
- `committee_profile` — who runs the committee, status, address
- `measure_spending` — spending tied to a ballot measure
- `vendor_revenue` — what vendors are paid across the system
- `donor_watch_since`, `upcoming_filings`, `filing_due_soon`, `committee_outlays_to`

## Day to day

| Task | Command |
|---|---|
| Start everything | `docker compose up -d` |
| Stop everything (data is safe) | `docker compose down` |
| Update the data (after the SOS publishes a new export) | `docker compose run --rm etl -- incremental` |
| Watch logs | `docker compose logs -f <db\|mcp\|etl>` |
| Open a psql prompt | `docker compose exec db psql -U cfdb -d cfdb` |

`incremental` re-downloads the export only if the SOS changed it; otherwise
it's a fast no-op. It's safe to run `etl` again after an interruption —
finished tables are skipped via checkpoints.

## Troubleshooting

- **The etl container gets killed while loading `rcpt_cd`** — the machine ran
  out of memory. Give Docker more RAM (16 GB recommended) and re-run the same
  command; it resumes.
- **Port 9527 is taken** — use another port: `MCP_PORT=9528 docker compose up -d mcp`
  (and point clients at 9528 instead).
- **I want a different database password** — set `DB_PASSWORD=***
  **before the first `docker compose up -d db`**. Changing it after data
  exists means rebuilding the `db` volume.
- **Start over from scratch** — `docker compose down -v` removes both
  volumes (database + download cache). Re-running steps 2-4 rebuilds
  everything.
- **Etl log says "Skipping X (already loaded...)"** — normal; that table was
  loaded in an earlier run and its file hasn't changed.

## What's in the database (quick map)

| Table | What it is | Size |
|---|---|---|
| `rcpt_cd` | Contributions (receipts) | ~20.2M rows |
| `expn_cd` | Expenditures | ~15.7M rows |
| `smry_cd` | Filing summaries (totals per form) | ~15.4M rows |
| `splt_cd` | Disbursement splits | ~6.3M rows |
| `s497_cd` | S497 form line items | ~1.39M rows |
| `filername_cd` | All committee/candidate names ever filed | ~1.34M rows |
| `filer_filings_cd` | Every filing a filer ever made | ~2.9M rows |
| `filers_cd`, `filer_*` | Committee identities, types, addresses, links | various |
| `text_memo_cd` | Free-text memos attached to filings | ~2.8M rows |
| `lobbyist_*`, `lpay_cd`, `lexp_cd`, ... | Lobbying disclosures & payments | various |
| `etl_dead_letter` | Audit log of source rows too broken to load | ~1,000 rows |
| `load_checkpoint` | Which files have been loaded (enables resume) | 1 per table |

All column names are the official SOS names in lowercase (e.g. `rcpt_date`,
`cmte_id`, `filing_id`). All text columns are `TEXT`; nothing is forced
NOT NULL except primary keys, because the source data regularly omits fields.

## Known source-data quirks (not bugs in this system)

- ~1,009 rows out of 80.7 million are malformed in the SOS export itself
  (columns shifted, or entirely empty). They're in `etl_dead_letter` with
  the full row data if you ever need them.
- 3 source tables are genuinely empty in the export (lobbyist history files).
- `filername_cd` has ~89 rows from a known SOS data-entry blemish; they load
  as-is.
