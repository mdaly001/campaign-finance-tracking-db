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

            # Combined yearly breakdown with proper aggregation
            sql = """
            SELECT
                COALESCE(r.year, e.year) AS year,
                COALESCE(r.contributions, 0) AS contributions,
                COALESCE(e.expenditures, 0) AS expenditures,
                COALESCE(r.contributions, 0) - COALESCE(e.expenditures, 0) AS net_position,
                COALESCE(r.contribution_count, 0) AS contribution_count,
                COALESCE(e.expenditure_count, 0) AS expenditure_count
            FROM (
                SELECT
                    cycle AS year,
                    ROUND(SUM(amount), 2) AS contributions,
                    COUNT(*) AS contribution_count
                FROM public.receipts_all
                WHERE filing_id IN (
                    SELECT filing_id FROM filer_filings_cd
                    WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
                )
                GROUP BY cycle
            ) r
            FULL OUTER JOIN (
                SELECT
                    cycle AS year,
                    ROUND(SUM(amount), 2) AS expenditures,
                    COUNT(*) AS expenditure_count
                FROM public.expn_all
                WHERE filing_id IN (
                    SELECT filing_id FROM filer_filings_cd
                    WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
                )
                GROUP BY cycle
            ) e ON r.year = e.year
            WHERE COALESCE(r.year, e.year) IS NOT NULL
            ORDER BY year;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== Progressive Era PAC — Yearly Contributions & Expenditures ===')
            print(get_text(result))

asyncio.run(run())
