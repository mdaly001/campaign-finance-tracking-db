# SOS CAL-ACCESS Data Loading Notes

## Database Schema

### Receipts Tables
- `rcpt_cd`: Periodic report receipts (20.2M rows)
- `s497_cd`: Form 497 24-hour large contributions (1.4M rows)
- `s498_cd`: Form 498 rapid-disclosure receipts (27.6K rows)

### Expenditure Tables
- `expn_cd`: Periodic expenditures (15.2M rows)
- `lexp_cd`: Linked expenditures (268K rows)
- `s496_cd`: Form 496 24-hour expenditures (75.7K rows)

### Metadata Tables
- `filername_cd`: Committee names (1.3M rows)
- `filer_xref_cd`: Committee ID mappings (cmte_id → filer_id)
- `filer_filings_cd`: Filing ID to filer ID mappings

## Critical Data Issues

### Issue 1: Stale cmte_id in Detail Tables
**Problem**: The `cmte_id` column in detail tables (`rcpt_cd`, `expn_cd`) often contains **stale/old committee IDs** that don't match the current `filer_xref_cd` mapping.

**Example**: Committee "CAIR ACTION PAC CALIFORNIA SPONSORED BY PROGRESSIVE ERA PAC"
- Current cmte_id: `1479907` (set in xref on 2025-03-25)
- Old cmte_id: `1449477` (original ID)
- Receipt row dated 2025-05-01 still has `cmte_id='1449477'` (OLD ID)
- Only 1 receipt found when querying `WHERE filing_id IN (SELECT filing_id FROM filer_filings_cd WHERE filer_id = 1479907)`

**Root Cause**: The SOS CAL-ACCESS export is a **point-in-time snapshot**. The xref mappings were updated after the data was exported, but the detail tables weren't retroactively updated.

**Workaround**: Always query detail tables via `filer_filings_cd` joins (not direct `cmte_id` matches):
```sql
-- Instead of:
SELECT * FROM rcpt_cd WHERE cmte_id = '1479907';

-- Use:
SELECT * FROM rcpt_cd 
WHERE filing_id IN (
    SELECT filing_id FROM filer_filings_cd 
    WHERE filer_id IN (
        SELECT filer_id FROM filer_xref_cd 
        WHERE xref_id = '1479907'
    )
);
```

### Issue 2: Date Formats
- `rcpt_cd.rcpt_date`: TIMESTAMP (stored as native SQL timestamp)
- `s497_cd.ctrib_date`: TIMESTAMP
- `s498_cd.date_rcvd`: TIMESTAMP
- All dates are properly typed—no numeric date parsing needed

### Issue 3: Missing Columns
- `lexp_cd` has 28 columns (no `cmte_id`)
- `s496_cd` has 13 columns (no payee name, only `expn_dscr`)

## MCP Server Notes

### Server Configuration
- Host: `192.168.87.41:9527`
- User: `cfdb_reader` (read-only)
- Database: `cfdb`
- Docker container: `172.18.0.2`

### MCP Tools
1. `run_sql` — Execute SELECT queries (read-only)
2. `describe_table` — Get table schema and row counts
3. `find_committees` — Find committees by name
4. 15 other analytical tools

### Direct DB Access (for migrations/ETL)
- User: `cfdb`
- Password: (in `.env`)
- Host: `192.168.87.41:5432`
- Database: `cfdb`

## ETL Process

### Running Full Load
```bash
cd /tmp/campaign-finance-tracking-db
source .venv/bin/activate
python -m state.etl full --database-url "postgresql://cfdb:cfdb_secure_pass_2026@192.168.87.41:5432/cfdb" --cache-dir "/tmp/campaign-finance-tracking-db/state/cache" --batch-size 500
```

### Resuming Incremental Load
```bash
cd /tmp/campaign-finance-tracking-db
source .venv/bin/activate
python -m state.etl resume --batch-size 500
```

### Daily Update (Cron)
- Job: "SOS Data Daily Update"
- Schedule: Every day at 6:00 AM PDT
- Runs: `python -m state.etl resume --batch-size 500`
- Job ID: `a6010df2763a`

## Common Queries

### Get contributions by committee (correct way)
```sql
SELECT donor_name, sum(amount) as total, count(*) as contributions
FROM receipts_all
WHERE filing_id IN (
    SELECT filing_id FROM filer_filings_cd
    WHERE filer_id IN (
        SELECT filer_id FROM filer_xref_cd
        WHERE xref_id = 'COMMITTEE_ID'
    )
)
GROUP BY donor_name
ORDER BY total DESC;
```

### Get committee profile
```sql
SELECT 
    n.naml AS committee_name,
    n.filer_type,
    n.status,
    x.xref_id AS cmte_id
FROM filer_xref_cd x
JOIN filername_cd n ON n.filer_id = x.filer_id
WHERE x.xref_id = 'COMMITTEE_ID'
ORDER BY x.effect_dt DESC LIMIT 1;
```

## Troubleshooting

### MCP Server Not Responding
- Check if container is running: `docker ps | grep mcp`
- Check logs: `docker logs campaign-finance-mcp`
- Restart: `docker compose restart mcp`

### Migration Failed
- Views must use explicit schema: `CREATE VIEW public.receipts_all`
- UNION ALL requires matching column counts in all branches
- Use `NULL` placeholders for missing columns

### ETL OOM Killed
- Reduce PostgreSQL memory: `work_mem=4MB`, `maintenance_work_mem=64MB`
- Reduce batch size: `--batch-size 100`
- Check swap: `swapon --show`

## File Locations

- Project: `/tmp/campaign-finance-tracking-db/`
- Cache: `/tmp/campaign-finance-tracking-db/state/cache/`
- Migrations: `/tmp/campaign-finance-tracking-db/migrations/`
- MCP Server: `http://192.168.87.41:9527/sse`
- MCP Tools: `http://192.168.87.41:9527/mcp`
