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

            # All Delaney contributions to Holstoge in rcpt_cd 2024
            sql1 = """
            SELECT
                rcpt.ctrib_naml AS donor_last,
                rcpt.ctrib_namf AS donor_first,
                rcpt.amount,
                rcpt.rcpt_date,
                rcpt.tran_id,
                rcpt.cmte_id AS receiving_cmte,
                rcpt.filing_id
            FROM rcpt_cd rcpt
            JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
            AND fn.naml ILIKE '%Holstoge%'
            AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
            ORDER BY rcpt.amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== Holstoge: All Delaney Contributions (rcpt_cd, 2024) ===')
            print(get_text(result))

            # Also check s497_cd
            sql2 = """
            SELECT
                s497.enty_naml AS donor_last,
                s497.enty_namf AS donor_first,
                s497.amount,
                s497.ctrib_date,
                s497.tran_id,
                s497.filing_id
            FROM s497_cd s497
            JOIN filer_filings_cd ff ON ff.filing_id = s497.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (s497.enty_naml ILIKE '%Delaney%' OR s497.enty_namf ILIKE '%Quinn%')
            AND fn.naml ILIKE '%Holstoge%'
            AND s497.ctrib_date >= '2024-01-01' AND s497.ctrib_date < '2025-01-01'
            ORDER BY s497.amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Holstoge: All Delaney Contributions (s497_cd, 2024) ===')
            print(get_text(result))

            # Get the unique filing_ids and their dates to see if there's a pattern
            sql3 = """
            SELECT DISTINCT
                rcpt.filing_id,
                ff.filing_date,
                COUNT(*) AS num_transactions,
                ROUND(SUM(rcpt.amount), 2) AS total
            FROM rcpt_cd rcpt
            JOIN filer_filings_cd ff ON ff.filing_id = rcpt.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE (rcpt.ctrib_naml ILIKE '%Delaney%' OR rcpt.ctrib_namf ILIKE '%Quinn%')
            AND fn.naml ILIKE '%Holstoge%'
            AND rcpt.rcpt_date >= '2024-01-01' AND rcpt.rcpt_date < '2025-01-01'
            GROUP BY rcpt.filing_id, ff.filing_date
            ORDER BY rcpt.filing_id;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Holstoge: Filing ID Breakdown ===')
            print(get_text(result))

            # Check if the donor name is actually "Holstoge" — maybe the query matched wrong
            sql4 = """
            SELECT
                fn.naml AS committee_name,
                fx.xref_id AS cmte_id,
                ff.filing_id,
                ff.filing_date
            FROM filer_filings_cd ff
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE fn.naml ILIKE '%Holstoge%'
            GROUP BY fn.naml, fx.xref_id, ff.filing_id, ff.filing_date
            ORDER BY ff.filing_id
            LIMIT 30;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== All Holstoge Filings in Database ===')
            print(get_text(result))

asyncio.run(run())
