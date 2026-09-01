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

            # Get top 5 by amount, last 10 years (2016-2026), deduplicating across rcpt_cd and s497_cd
            # Both tables have the same tran_id for duplicate entries
            sql = """
            SELECT
                donor AS name,
                first_name,
                rcpt_date AS date,
                EXTRACT(YEAR FROM rcpt_date)::INTEGER AS year,
                amount,
                tran_id AS unique_id,
                'rcpt_cd' AS source
            FROM rcpt_cd
            WHERE (ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%')
            AND rcpt_date >= CURRENT_DATE - INTERVAL '10 years'
            UNION ALL
            SELECT
                donor,
                first_name,
                ctrib_date,
                EXTRACT(YEAR FROM ctrib_date)::INTEGER,
                amount,
                tran_id,
                's497_cd' AS source
            FROM s497_cd
            WHERE (enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%')
            AND ctrib_date >= CURRENT_DATE - INTERVAL '10 years';
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== All Delaney/Quinn Contributions Last 10 Years (before dedup) ===')
            print(get_text(result)[:3000])
            
            # Now get top 5 unique by amount
            sql2 = """
            WITH all_contributions AS (
                SELECT DISTINCT ON (tran_id)
                    ctrib_naml AS name,
                    ctrib_namf AS first_name,
                    rcpt_date AS date,
                    EXTRACT(YEAR FROM rcpt_date)::INTEGER AS year,
                    amount,
                    tran_id AS unique_id
                FROM rcpt_cd
                WHERE (ctrib_naml ILIKE '%Delaney%' OR ctrib_namf ILIKE '%Quinn%')
                AND rcpt_date >= CURRENT_DATE - INTERVAL '10 years'
                
                UNION ALL
                
                SELECT DISTINCT ON (tran_id)
                    enty_naml AS name,
                    enty_namf AS first_name,
                    ctrib_date AS date,
                    EXTRACT(YEAR FROM ctrib_date)::INTEGER AS year,
                    amount,
                    tran_id AS unique_id
                FROM s497_cd
                WHERE (enty_naml ILIKE '%Delaney%' OR enty_namf ILIKE '%Quinn%')
                AND ctrib_date >= CURRENT_DATE - INTERVAL '10 years'
            )
            SELECT * FROM all_contributions
            ORDER BY amount DESC
            LIMIT 5;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Top 5 Contributions by Quinn Delaney (Last 10 Years: 2016-2026) ===')
            print(get_text(result))

asyncio.run(run())
