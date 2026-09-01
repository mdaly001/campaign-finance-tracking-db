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

            # Check what columns the expn_all view actually has
            result = await session.call_tool('describe_table', {'table_name': 'expn_all'})
            print('=== expn_all view columns ===')
            print(get_text(result))
            
            # Direct query from expn_cd to check if expn_dscr exists
            sql2 = "SELECT expn_dscr, COUNT(*), SUM(amount) FROM expn_cd WHERE filing_id IN (SELECT filing_id FROM filer_filings_cd WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')) GROUP BY expn_dscr ORDER BY SUM(amount) DESC LIMIT 10;"
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Expenditure descriptions from expn_cd ===')
            print(get_text(result))

asyncio.run(run())
