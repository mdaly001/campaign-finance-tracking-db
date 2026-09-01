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

            # Top 5 donors to Progressive Era PAC (cmte_id 1479907)
            # Key: must use filer_filings_cd join because cmte_id in detail tables
            # is often stale (the committee was renamed from 1449477 to 1479907)
            sql = """
            SELECT
                donor_naml AS donor_name,
                COUNT(*) AS contribution_count,
                ROUND(SUM(amount), 2) AS total_amount
            FROM public.receipts_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (
                    SELECT filer_id FROM filer_xref_cd
                    WHERE xref_id = '1479907'
                )
            )
            GROUP BY donor_naml
            HAVING donor_naml IS NOT NULL AND donor_naml != ''
            ORDER BY total_amount DESC
            LIMIT 5;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== Top 5 Donors to Progressive Era PAC ===')
            print(get_text(result))

asyncio.run(run())
