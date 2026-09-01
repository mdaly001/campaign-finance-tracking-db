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

            # Search for "Progressive Era PAC" as primary name (not just substring)
            # Use ILIKE to find rows where the name IS "Progressive Era PAC"
            sql1 = """
            SELECT naml, namf, filer_id FROM filername_cd
            WHERE naml ILIKE '%Progressive Era PAC%'
            AND naml NOT ILIKE '%SPONSORED BY PROGRESSIVE ERA PAC%'
            LIMIT 10;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== filer names with "Progressive Era PAC" (excluding sponsored) ===')
            print(get_text(result))
            
            # Search for exact name
            sql2 = """
            SELECT naml, namf, filer_id FROM filername_cd
            WHERE naml ILIKE 'PROGRESSIVE ERA PAC'
            OR namf ILIKE 'PROGRESSIVE ERA PAC'
            OR naml ILIKE 'PROGRESSIVE' AND namf ILIKE 'ERA PAC'
            LIMIT 10;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== filer names with "PROGRESSIVE ERA PAC" ===')
            print(get_text(result))
            
            # Check if there's a committee with just "Progressive Era PAC" in the name
            sql3 = """
            SELECT x.xref_id, n.naml, n.namf, n.filer_type, n.status
            FROM filer_xref_cd x
            JOIN filername_cd n ON n.filer_id = x.filer_id
            WHERE n.naml ILIKE 'Progressive Era PAC'
            LIMIT 10;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Committee matching "Progressive Era PAC" exactly ===')
            print(get_text(result))
            
            # Check for any committee with "progressive" in the name
            sql4 = """
            SELECT x.xref_id, n.naml, n.namf, n.filer_type, n.status, 
                   MAX(x.effect_dt) as last_updated
            FROM filer_xref_cd x
            JOIN filername_cd n ON n.filer_id = x.filer_id
            WHERE n.naml ILIKE '%progressive%'
            GROUP BY x.xref_id, n.naml, n.namf, n.filer_type, n.status
            ORDER BY n.naml
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== All "progressive" committees ===')
            print(get_text(result))

asyncio.run(run())
