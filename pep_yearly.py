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

            # Yearly contributions
            sql1 = """
            SELECT
                cycle AS year,
                COUNT(*) AS contribution_count,
                ROUND(SUM(amount), 2) AS total_contributions
            FROM public.receipts_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY cycle
            ORDER BY cycle;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== Yearly Contributions ===')
            print(get_text(result))
            
            # Yearly expenditures
            sql2 = """
            SELECT
                cycle AS year,
                COUNT(*) AS expenditure_count,
                ROUND(SUM(amount), 2) AS total_expenditures
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY cycle
            ORDER BY cycle;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Yearly Expenditures ===')
            print(get_text(result))
            
            # Combined net position
            sql3 = """
            SELECT
                sub.year,
                COALESCE(sub.contributions, 0) AS contributions,
                COALESCE(sub.expenditures, 0) AS expenditures,
                COALESCE(sub.contributions, 0) - COALESCE(sub.expenditures, 0) AS net_position,
                COALESCE(sub.contrib_count, 0) AS contribution_count,
                COALESCE(sub.exp_count, 0) AS expenditure_count
            FROM (
                SELECT cycle AS year, SUM(amount) AS contributions, COUNT(*) AS contrib_count, NULL AS expenditures, NULL AS exp_count
                FROM public.receipts_all
                WHERE filing_id IN (
                    SELECT filing_id FROM filer_filings_cd
                    WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
                )
                GROUP BY cycle
                UNION ALL
                SELECT cycle AS year, NULL AS contributions, NULL AS contrib_count, SUM(amount) AS expenditures, COUNT(*) AS exp_count
                FROM public.expn_all
                WHERE filing_id IN (
                    SELECT filing_id FROM filer_filings_cd
                    WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
                )
                GROUP BY cycle
            ) sub
            ORDER BY year;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Combined Yearly Breakdown ===')
            print(get_text(result))

asyncio.run(run())
