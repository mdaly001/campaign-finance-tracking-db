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

            # Check receipts in each source table for this filer's filing_ids
            # 1. rcpt_cd
            sql1 = """
            SELECT ctrib_naml AS donor_name, COUNT(*) AS cnt, ROUND(SUM(amount), 2) AS total
            FROM rcpt_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1479907')
            )
            GROUP BY ctrib_naml
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== rcpt_cd donors ===')
            print(get_text(result))
            
            # 2. s497_cd (24-hour large contributions)
            sql2 = """
            SELECT enty_naml AS donor_name, COUNT(*) AS cnt, ROUND(SUM(amount), 2) AS total
            FROM s497_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1479907')
            )
            GROUP BY enty_naml
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== s497_cd donors ===')
            print(get_text(result))
            
            # 3. s498_cd (498 receipts)
            sql3 = """
            SELECT payor_naml AS donor_name, COUNT(*) AS cnt, ROUND(SUM(amt_rcvd), 2) AS total
            FROM s498_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1479907')
            )
            GROUP BY payor_naml
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== s498_cd donors ===')
            print(get_text(result))
            
            # 4. Total filing count for this committee
            sql4 = """
            SELECT COUNT(*) FROM filer_filings_cd
            WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1479907');
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== Total filings for this committee ===')
            print(get_text(result))
            
            # 5. Check the filing_ids and their forms
            sql5 = """
            SELECT filing_id, filing_date, form_type FROM filer_filings_cd
            WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1479907')
            ORDER BY filing_date;
            """
            result = await session.call_tool('run_sql', {'sql': sql5})
            print('\n=== All filings for this committee ===')
            print(get_text(result))

asyncio.run(run())
