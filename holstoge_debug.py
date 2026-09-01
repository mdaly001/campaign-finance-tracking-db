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

            # Check the raw rcpt_cd columns to understand the schema
            sql0 = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'rcpt_cd'
            ORDER BY ordinal_position;
            """
            result = await session.call_tool('run_sql', {'sql': sql0})
            print('=== rcpt_cd full schema ===')
            print(get_text(result))

            # Check if the receiving committee name is stored differently
            # Look at a sample row from Delaney contributions to understand structure
            sql1 = """
            SELECT
                filing_id,
                ctrib_naml,
                ctrib_namf,
                amount,
                rcpt_date,
                tran_id,
                cmte_id,
                intr_cmteid
            FROM rcpt_cd
            WHERE ctrib_naml ILIKE '%Delaney%'
            AND rcpt_date >= '2024-01-01' AND rcpt_date < '2025-01-01'
            ORDER BY amount DESC
            LIMIT 10;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('\n=== Sample Delaney rcpt_cd rows (raw) ===')
            print(get_text(result))

            # The original query used the filing chain join. Let's see what committee name
            # the JOIN actually resolved to for these filing_ids
            sql2 = """
            SELECT DISTINCT
                rcpt.filing_id,
                fn.naml AS resolved_committee_name,
                fx.xref_id,
                COUNT(*) AS num_rows,
                ROUND(SUM(rcpt.amount), 2) AS total_amount
            FROM rcpt_cd rcpt
            JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
            AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
            GROUP BY rcpt.filing_id, fn.naml, fx.xref_id
            ORDER BY total_amount DESC
            LIMIT 30;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== What the JOIN chain actually resolves to ===')
            print(get_text(result))

            # Let's check if the filing_id in rcpt_cd refers to the recipient committee
            # by looking at whether the resolved name matches the donor or recipient
            sql3 = """
            SELECT
                'same as donor' AS category,
                COUNT(*) AS rows
            FROM rcpt_cd rcpt
            JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
            AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
            AND (fn.naml ILIKE '%Delaney%' OR fn.naml ILIKE '%Quinn%' OR fn.naml ILIKE '%Jordan%')
            
            UNION ALL
            
            SELECT
                'NOT same as donor (receiving committee)' AS category,
                COUNT(*) AS rows
            FROM rcpt_cd rcpt
            JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
            AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
            AND NOT (fn.naml ILIKE '%Delaney%' OR fn.naml ILIKE '%Quinn%' OR fn.naml ILIKE '%Jordan%');
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Does filing_id resolve to donor or recipient? ===')
            print(get_text(result))

asyncio.run(run())
