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

            # Find Hilltop Public Solutions in the committee registry
            result = await session.call_tool('find_committees', {'name': 'Hilltop Public Solutions'})
            print('=== find_committees for "Hilltop Public Solutions" ===')
            print(get_text(result))
            
            # Search for it in filername
            sql1 = """
            SELECT x.xref_id, n.naml, n.namf, n.filer_type, n.status
            FROM filer_xref_cd x
            JOIN filername_cd n ON n.filer_id = x.filer_id
            WHERE n.naml ILIKE '%Hilltop%'
            LIMIT 10;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('\n=== filername match for "Hilltop" ===')
            print(get_text(result))

asyncio.run(run())
