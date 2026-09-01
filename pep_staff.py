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

            # All staff time / consulting / professional service expenditures
            # across all years, with payee, purpose, amount, date, cycle
            sql = """
            SELECT
                expn_dscr AS purpose,
                payee_naml AS payee,
                amount,
                expn_date,
                EXTRACT(YEAR FROM expn_date)::INTEGER AS year
            FROM expn_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            AND (
                expn_dscr ILIKE '%staff%'
                OR expn_dscr ILIKE '%consult%'
                OR expn_dscr ILIKE '%contract%'
                OR expn_dscr ILIKE '%planning%'
                OR expn_dscr ILIKE '%professional%'
                OR expn_dscr ILIKE '%training%'
                OR expn_dscr ILIKE '%cohort%'
                OR expn_dscr ILIKE '%outreach%'
                OR expn_dscr ILIKE '%school board%'
            )
            ORDER BY year, expn_date;
            """
            result = await session.call_tool('run_sql', {'sql': sql})
            print('=== All Staff Time / Consulting / Professional Expenditures ===')
            print(get_text(result))
            
            # Summary by year
            sql2 = """
            SELECT
                EXTRACT(YEAR FROM expn_date)::INTEGER AS year,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total,
                ROUND(AVG(amount), 2) AS avg_amount
            FROM expn_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            AND (
                expn_dscr ILIKE '%staff%'
                OR expn_dscr ILIKE '%consult%'
                OR expn_dscr ILIKE '%contract%'
                OR expn_dscr ILIKE '%planning%'
                OR expn_dscr ILIKE '%professional%'
                OR expn_dscr ILIKE '%training%'
                OR expn_dscr ILIKE '%cohort%'
                OR expn_dscr ILIKE '%outreach%'
                OR expn_dscr ILIKE '%school board%'
            )
            GROUP BY year
            ORDER BY year;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== Staff/Consulting/Professional Expenditures by Year ===')
            print(get_text(result))
            
            # Summary by payee
            sql3 = """
            SELECT
                payee_naml AS payee,
                COUNT(*) AS cnt,
                ROUND(SUM(amount), 2) AS total,
                ROUND(AVG(amount), 2) AS avg_amount
            FROM expn_cd
            WHERE filing_id IN (
                SELECT filing_id FROM filer_filings_cd
                WHERE filer_id IN (SELECT filer_id FROM filer_xref_cd WHERE xref_id = '1449477')
            )
            AND (
                expn_dscr ILIKE '%staff%'
                OR expn_dscr ILIKE '%consult%'
                OR expn_dscr ILIKE '%contract%'
                OR expn_dscr ILIKE '%planning%'
                OR expn_dscr ILIKE '%professional%'
                OR expn_dscr ILIKE '%training%'
                OR expn_dscr ILIKE '%cohort%'
                OR expn_dscr ILIKE '%outreach%'
                OR expn_dscr ILIKE '%school board%'
            )
            GROUP BY payee_naml
            HAVING payee_naml IS NOT NULL AND payee_naml != ''
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Staff/Consulting/Professional Expenditures by Payee ===')
            print(get_text(result))

asyncio.run(run())
