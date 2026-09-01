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

            # Aggregate by cmte_id - dedup across filing_id variations
            sql = """
            SELECT
                fx.xref_id AS cmte_id,
                MAX(fn.naml) AS committee_name,
                MAX(fn.filer_type) AS committee_type,
                COUNT(DISTINCT e.filing_id) AS total_filing_ids,
                COUNT(*) AS total_transactions,
                ROUND(SUM(e.amount), 2) AS total_hilltop_paid,
                MIN(EXTRACT(YEAR FROM e.expn_date)::INTEGER) AS earliest_year,
                MAX(EXTRACT(YEAR FROM e.expn_date)::INTEGER) AS latest_year
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            GROUP BY fx.xref_id
            HAVING SUM(e.amount) > 0
            ORDER BY total_hilltop_paid DESC
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== Hilltop Public Solutions — Top Clients by Total Paid ===')
            print(get_text(result))
            
            # Also check if any of these are the same entity (e.g., Newsom's campaigns + ballot measure committees)
            # Get unique filer_ids for the top committees
            sql2 = """
            SELECT
                fx.xref_id AS cmte_id,
                fn.naml AS committee_name
            FROM filer_xref_cd fx
            JOIN filername_cd fn ON fn.filer_id = fx.filer_id
            WHERE fx.xref_id IN ('1380675', '1357909', '1421884', '1462796', '1450340', '741666', '1340742', '1414018', '1456428', '1437201')
            ORDER BY fx.xref_id;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Committee Details ===')
            print(get_text(result))

asyncio.run(run())
