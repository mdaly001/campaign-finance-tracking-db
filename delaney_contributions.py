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

            # Search for "Delaney" as contributor across all receipt tables
            # Note: rcpt_date is a TIMESTAMP, not numeric
            
            # 1. rcpt_cd — periodic receipts
            sql1 = """
            SELECT
                ctrib_naml AS donor,
                ctrib_namf AS first_name,
                ctrib_namt AS middle,
                ctrib_dscr AS org,
                cmte_id AS receiving_committee,
                rcpt_date,
                EXTRACT(YEAR FROM rcpt_date)::INTEGER AS year,
                amount,
                tran_id
            FROM rcpt_cd
            WHERE ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%'
            AND rcpt_date >= CURRENT_DATE - INTERVAL '10 years'
            ORDER BY amount DESC
            LIMIT 50;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== rcpt_cd: Delaney/Quinn Contributions ===')
            print(get_text(result))

            # 2. s497_cd — 24-hour large contributions
            sql2 = """
            SELECT
                enty_naml AS donor,
                enty_namf AS first_name,
                cmte_id AS receiving_committee,
                ctrib_date,
                EXTRACT(YEAR FROM ctrib_date)::INTEGER AS year,
                amount,
                tran_id
            FROM s497_cd
            WHERE enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%'
            AND ctrib_date >= CURRENT_DATE - INTERVAL '10 years'
            ORDER BY amount DESC
            LIMIT 50;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== s497_cd: Delaney/Quinn Large Contributions ===')
            print(get_text(result))

            # 3. s498_cd — rapid disclosures
            sql3 = """
            SELECT
                payor_naml AS donor,
                payor_namf AS first_name,
                cmte_id AS receiving_committee,
                date_rcvd,
                EXTRACT(YEAR FROM date_rcvd)::INTEGER AS year,
                amt_rcvd,
                tran_id
            FROM s498_cd
            WHERE payor_naml ILIKE '%Delaney%' OR payor_namf ILIKE '%Quinn%'
            AND date_rcvd >= CURRENT_DATE - INTERVAL '10 years'
            ORDER BY amt_rcvd DESC
            LIMIT 50;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== s498_cd: Delaney/Quinn Rapid Disclosures ===')
            print(get_text(result))

            # 4. Summary counts and totals
            sql4 = """
            SELECT 'rcpt_cd' AS source, 
                   COUNT(*) AS total_contributions, 
                   ROUND(SUM(amount), 2) AS total_amount,
                   MIN(EXTRACT(YEAR FROM rcpt_date)::INTEGER) AS earliest,
                   MAX(EXTRACT(YEAR FROM rcpt_date)::INTEGER) AS latest
            FROM rcpt_cd
            WHERE ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%'
            
            UNION ALL
            
            SELECT 's497_cd', 
                   COUNT(*), 
                   ROUND(SUM(amount), 2),
                   MIN(EXTRACT(YEAR FROM ctrib_date)::INTEGER),
                   MAX(EXTRACT(YEAR FROM ctrib_date)::INTEGER)
            FROM s497_cd
            WHERE enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%';
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== Summary: All Delaney/Quinn Contributions ===')
            print(get_text(result))

asyncio.run(run())
