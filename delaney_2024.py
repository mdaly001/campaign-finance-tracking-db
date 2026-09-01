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

            # 2024 Delaney contributions: amount breakdown
            sql1 = """
            SELECT
                ctrib_naml AS name,
                ctrib_namf AS first_name,
                amount,
                rcpt_date,
                tran_id
            FROM rcpt_cd
            WHERE (ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%')
            AND rcpt_date >= '2024-01-01' AND rcpt_date < '2025-01-01'
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== rcpt_cd: 2024 Delaney Contributions ===')
            print(get_text(result))

            # 2024 s497_cd contributions
            sql2 = """
            SELECT
                enty_naml AS name,
                enty_namf AS first_name,
                amount,
                ctrib_date,
                tran_id
            FROM s497_cd
            WHERE (enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%')
            AND ctrib_date >= '2024-01-01' AND ctrib_date < '2025-01-01'
            ORDER BY amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== s497_cd: 2024 Delaney Contributions ===')
            print(get_text(result))

            # 2024 summary
            sql3 = """
            SELECT 'rcpt_cd' AS source, COUNT(*) AS contributions, ROUND(SUM(amount),2) AS total
            FROM rcpt_cd WHERE (ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%')
            AND rcpt_date >= '2024-01-01' AND rcpt_date < '2025-01-01'
            UNION ALL
            SELECT 's497_cd', COUNT(*), ROUND(SUM(amount),2)
            FROM s497_cd WHERE (enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%')
            AND ctrib_date >= '2024-01-01' AND ctrib_date < '2025-01-01'
            UNION ALL
            SELECT 'rcpt_cd + s497_cd (combined)', COUNT(*), ROUND(SUM(amount),2)
            FROM (
                SELECT ctrib_naml, ctrib_namf, amount, rcpt_date
                FROM rcpt_cd WHERE (ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%')
                AND rcpt_date >= '2024-01-01' AND rcpt_date < '2025-01-01'
                UNION ALL
                SELECT enty_naml, enty_namf, amount, ctrib_date
                FROM s497_cd WHERE (enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%')
                AND ctrib_date >= '2024-01-01' AND ctrib_date < '2025-01-01'
            ) t;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== 2024 Summary ===')
            print(get_text(result))

            # Breakdown by month
            sql4 = """
            SELECT EXTRACT(MONTH FROM rcpt_date)::INTEGER AS month,
                   COUNT(*) AS contributions, ROUND(SUM(amount),2) AS total
            FROM rcpt_cd
            WHERE (ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%')
            AND rcpt_date >= '2024-01-01' AND rcpt_date < '2025-01-01'
            GROUP BY month ORDER BY month;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== rcpt_cd: Monthly Breakdown 2024 ===')
            print(get_text(result))

            sql5 = """
            SELECT EXTRACT(MONTH FROM ctrib_date)::INTEGER AS month,
                   COUNT(*) AS contributions, ROUND(SUM(amount),2) AS total
            FROM s497_cd
            WHERE (enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%')
            AND ctrib_date >= '2024-01-01' AND ctrib_date < '2025-01-01'
            GROUP BY month ORDER BY month;
            """
            result = await session.call_tool('run_sql', {'sql': sql5})
            print('\n=== s497_cd: Monthly Breakdown 2024 ===')
            print(get_text(result))

asyncio.run(run())
