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

            # Check the expenditure descriptions/purposes for the top payees
            # to see if these are contributions to other committees
            sql = """
            SELECT
                payee_naml AS payee,
                expn_dscr AS purpose,
                amount,
                expn_date,
                memo_code,
                memo_refno
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            AND payee_naml IN (
                'Battleground California',
                'Working Families for Safe Neighborhoods & George Gascon for District Attorney 2024',
                'Courage California State PAC',
                'CA Working Families Party',
                'Orange County PAC',
                'California Donor Table_501c4'
            )
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== Expenditure Details for Top Payees ===')
            print(get_text(result))
            
            # Check all unique expenditure descriptions to understand categories
            sql2 = """
            SELECT
                expn_dscr AS purpose,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total,
                ROUND(AVG(amount), 2) AS avg_amount
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY expn_dscr
            HAVING expn_dscr IS NOT NULL AND expn_dscr != ''
            ORDER BY total DESC
            LIMIT 50;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== All Expenditure Purposes ===')
            print(get_text(result))

asyncio.run(run())
