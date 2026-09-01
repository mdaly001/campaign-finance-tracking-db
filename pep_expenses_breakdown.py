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

            # Check what expenditure types exist for this committee
            sql1 = """
            SELECT
                expn_dscr AS purpose,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total,
                ROUND(AVG(amount), 2) AS avg_amount
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY expn_dscr
            HAVING expn_dscr IS NOT NULL AND expn_dscr != ''
            ORDER BY total DESC
            LIMIT 30;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== Expenditure Purposes (by description) ===')
            print(get_text(result))
            
            # Check top 10 payees
            sql2 = """
            SELECT
                COALESCE(payee_naml, '(no payee name)') AS payee_name,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY payee_naml
            HAVING payee_naml IS NOT NULL AND payee_naml != ''
            ORDER BY total DESC
            LIMIT 15;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Top 10 Payees ===')
            print(get_text(result))
            
            # Check if there are contributions TO other committees (contributions out)
            # These would typically have a purpose/description indicating a contribution to another entity
            sql3 = """
            SELECT
                expn_dscr AS purpose,
                payee_naml,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            AND (
                expn_dscr ILIKE '%contribution%'
                OR expn_dscr ILIKE '%transfer%'
                OR expn_dscr ILIKE '%to%'
                OR expn_dscr ILIKE '%donation%'
            )
            GROUP BY expn_dscr, payee_naml
            ORDER BY total DESC
            LIMIT 20;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Possible "Contributions Out" ===')
            print(get_text(result))
            
            # Check the form_type breakdown for expenditures
            sql4 = """
            SELECT form_type, COUNT(*) AS cnt, ROUND(SUM(amount), 2) AS total
            FROM public.expn_all
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            GROUP BY form_type
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== Expenditures by Form Type ===')
            print(get_text(result))

asyncio.run(run())
