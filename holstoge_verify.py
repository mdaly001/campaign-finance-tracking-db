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

            # Get the actual transactions for Holstoge (filing_id 2956865)
            sql1 = """
            SELECT
                ctrib_naml AS donor_last,
                ctrib_namf AS donor_first,
                ctrib_namt AS donor_middle,
                amount,
                rcpt_date,
                tran_id,
                memo_code,
                memo_refno,
                cmte_id
            FROM rcpt_cd
            WHERE filing_id = 2956865
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== Holstoge (filing_id 2956865): Actual Donors ===')
            print(get_text(result))

            # Get filing date
            sql2 = """
            SELECT filing_id, filing_date
            FROM filer_filings_cd
            WHERE filing_id = 2956865;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Holstoge Filing Date ===')
            print(get_text(result))

            # Let's also check: how many transactions are actually from "Quinn Delaney" 
            # vs other donors in that filing
            sql3 = """
            SELECT
                CASE 
                    WHEN ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%' OR ctrib_naml ILIKE '%Jordan%'
                    THEN 'Delaney/Jordan family'
                    ELSE 'Other donor'
                END AS donor_group,
                COUNT(*) AS num_transactions,
                ROUND(SUM(amount), 2) AS total_amount
            FROM rcpt_cd
            WHERE filing_id = 2956865
            GROUP BY donor_group;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Holstoge: How Much is from Delaney vs Others? ===')
            print(get_text(result))

            # Now let's see: does the $198K from Holstoge come ONLY from Delaney?
            # And what is the average amount?
            sql4 = """
            SELECT
                amount,
                COUNT(*) AS num_transactions,
                ROUND(SUM(amount), 2) AS total_for_amount
            FROM rcpt_cd
            WHERE filing_id = 2956865
            GROUP BY amount
            ORDER BY amount;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== Holstoge: Amount Distribution ===')
            print(get_text(result))

asyncio.run(run())
