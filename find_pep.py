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

            # Find committees matching "Progressive Era PAC" exactly
            result = await session.call_tool('find_committees', {'name': 'Progressive Era PAC'})
            print('=== find_committees for "Progressive Era PAC" ===')
            print(get_text(result))
            
            # Also try just "Progressive Era" to see all matches
            result = await session.call_tool('find_committees', {'name': 'Progressive Era'})
            print('\n=== find_committees for "Progressive Era" ===')
            print(get_text(result))

asyncio.run(run())
