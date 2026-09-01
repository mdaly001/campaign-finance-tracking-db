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

            # Check raw expn_cd expenditure descriptions for this committee
            sql = """
            SELECT
                expn_dscr AS purpose,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total,
                ROUND(AVG(amount), 2) AS avg_amount
            FROM expn_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY expn_dscr
            HAVING expn_dscr IS NOT NULL AND expn_dscr != ''
            ORDER BY total DESC
            LIMIT 30;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== Expenditure Purposes (raw expn_cd) ===')
            print(get_text(result))
            
            # Check memo fields for contribution indicators
            sql2 = """
            SELECT
                memo_code,
                memo_refno,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total
            FROM expn_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY memo_code, memo_refno
            HAVING memo_code IS NOT NULL AND memo_code != ''
            ORDER BY total DESC
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Memo Codes/References ===')
            print(get_text(result))
            
            # Check specific top payees with their purposes
            sql3 = """
            SELECT
                payee_naml AS payee,
                expn_dscr AS purpose,
                memo_code,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total
            FROM expn_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            AND payee_naml IN (
                'Battleground California',
                'Working Families for Safe Neighborhoods & George Gascon for District Attorney 2024',
                'Courage California State PAC',
                'CA Working Families Party',
                'California Donor Table_501c4'
            )
            GROUP BY payee_naml, expn_dscr, memo_code
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Top Payees with Expenditure Purposes ===')
            print(get_text(result))

asyncio.run(run())
