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

            # Check what columns exist in rcpt_cd to find the receiving committee
            sql0 = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'rcpt_cd'
            AND (column_name ILIKE '%cmte%' OR column_name ILIKE '%commit%' OR column_name ILIKE '%recip%' OR column_name ILIKE '%to%')
            ORDER BY column_name;
            """
            result = await session.call_tool('run_sql', {'sql': sql0})
            print('=== rcpt_cd columns related to committee ===')
            print(get_text(result))

            # Top 20 committees by total received from Delaney in 2024 (rcpt_cd)
            sql1 = """
            SELECT
                rcpt.cmte_id AS receiving_cmte_id,
                fn.naml AS committee_name,
                COUNT(*) AS num_contributions,
                ROUND(SUM(rcpt.amount), 2) AS total_received
            FROM rcpt_cd rcpt
            JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
            AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
            GROUP BY rcpt.cmte_id, fn.naml
            ORDER BY total_received DESC
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('\n=== Top 20 Committees by Delaney Contributions (rcpt_cd, 2024) ===')
            print(get_text(result))

            # Same for s497_cd
            sql2 = """
            SELECT
                s497.cmte_id AS receiving_cmte_id,
                fn.naml AS committee_name,
                COUNT(*) AS num_contributions,
                ROUND(SUM(s497.amount), 2) AS total_received
            FROM s497_cd s497
            JOIN filer_filings_cd ff ON ff.filing_id = s497.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (s497.enty_naml ILIKE '%Delaney%' OR s497.enty_namf ILIKE '%Quinn%')
            AND s497.ctrib_date >= '2024-01-01' AND s497.ctrib_date < '2025-01-01'
            GROUP BY s497.cmte_id, fn.naml
            ORDER BY total_received DESC
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Top 20 Committees by Delaney Contributions (s497_cd, 2024) ===')
            print(get_text(result))

            # Combined by cmte_id across both tables
            sql3 = """
            SELECT
                cmte_id,
                MAX(committee_name) AS committee_name,
                SUM(num_contributions) AS total_contributions,
                ROUND(SUM(total_received), 2) AS total_received
            FROM (
                SELECT
                    rcpt.cmte_id,
                    fn.naml AS committee_name,
                    COUNT(*) AS num_contributions,
                    SUM(rcpt.amount) AS total_received
                FROM rcpt_cd rcpt
                JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
                JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
                JOIN filername_cd fn ON fn.filer_id = ff.filer_id
                WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
                AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
                GROUP BY rcpt.cmte_id, fn.naml
                
                UNION ALL
                
                SELECT
                    s497.cmte_id,
                    fn.naml,
                    COUNT(*),
                    SUM(s497.amount)
                FROM s497_cd s497
                JOIN filer_filings_cd ff ON ff.filing_id = s497.filing_id
                JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
                JOIN filername_cd fn ON fn.filer_id = ff.filer_id
                WHERE (s497.enty_naml ILIKE '%Delaney%' OR s497.enty_namf ILIKE '%Quinn%')
                AND s497.ctrib_date >= '2024-01-01' AND s497.ctrib_date < '2025-01-01'
                GROUP BY s497.cmte_id, fn.naml
            ) sub
            GROUP BY cmte_id
            ORDER BY total_received DESC
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Combined Top 20 Committees (rcpt_cd + s497_cd, 2024) ===')
            print(get_text(result))

asyncio.run(run())
