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

            # Hilltop Public Solutions is likely a consulting firm, not a PAC/committee
            # So it would show up as a PAYEE in the expenditure tables
            # Search for it across all expenditure payees
            
            # 1. Search expn_cd for Hilltop as payee
            sql1 = """
            SELECT
                payee_naml AS payee,
                expn_dscr AS purpose,
                amount,
                filing_id,
                expn_date
            FROM expn_cd
            WHERE payee_naml ILIKE '%hilltop%'
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== "Hilltop" as payee in expn_cd ===')
            print(get_text(result))
            
            # 2. Search lexp_cd for Hilltop as payee
            sql2 = """
            SELECT
                payee_naml AS payee,
                expn_dscr AS purpose,
                amount,
                filing_id,
                expn_date
            FROM lexp_cd
            WHERE payee_naml ILIKE '%hilltop%'
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== "Hilltop" as payee in lexp_cd ===')
            print(get_text(result))
            
            # 3. Search s496_cd for Hilltop (no payee name field but maybe in description)
            sql3 = """
            SELECT
                expn_dscr,
                amount,
                filing_id,
                exp_date
            FROM s496_cd
            WHERE expn_dscr ILIKE '%hilltop%'
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== "Hilltop" in s496_cd descriptions ===')
            print(get_text(result))

asyncio.run(run())
