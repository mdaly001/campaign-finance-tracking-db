import asyncio
from mcp.client.streamable_http import streamable_http_client
from mcp.client import ClientSession

async def run():
    async with streamable_http_client('http://192.168.87.41:9527/mcp') as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            def get_text(result):
                for c in result.content:
                    if hasattr(c, 'text'):
                        return c.text
                return str(result.content)

            # Get distinct filing_ids paying Hilltop, with their committee info
            sql = """
            SELECT DISTINCT
                ff.filing_id,
                ff.filer_id,
                fx.xref_id AS cmte_id,
                fn.naml AS committee_name,
                COUNT(*) AS hilltop_tx_count,
                ROUND(SUM(e.amount), 2) AS total_hilltop,
                MIN(EXTRACT(YEAR FROM e.expn_date)::INTEGER) AS earliest_year,
                MAX(EXTRACT(YEAR FROM e.expn_date)::INTEGER) AS latest_year
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            GROUP BY ff.filing_id, ff.filer_id, fx.xref_id, fn.naml
            HAVING SUM(e.amount) > 0
            ORDER BY total_hilltop DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== Hilltop Public Solutions — Clients by Total Amount ===')
            print(get_text(result))

asyncio.run(run())
