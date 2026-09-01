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

            # Yearly breakdown per committee
            sql1 = """
            SELECT
                fx.xref_id AS cmte_id,
                EXTRACT(YEAR FROM e.expn_date) AS y,
                COUNT(*) AS tx_count,
                ROUND(SUM(e.amount), 2) AS total
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            AND fx.xref_id IN ('1421884', '1462796')
            AND e.expn_date IS NOT NULL
            GROUP BY fx.xref_id, y
            HAVING y IS NOT NULL
            ORDER BY fx.xref_id, y;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== Hilltop Spending by Year (CAA Committees) ===')
            print(get_text(result))

            # Get all filing details for these 2 committees
            sql2 = """
            SELECT
                filing_id,
                filing_date
            FROM filer_filings_cd
            WHERE filer_id IN (
                SELECT filer_id FROM filer_xref_cd
                WHERE xref_id IN ('1421884', '1462796')
            )
            AND filing_date >= '2024-01-01'
            ORDER BY filing_id;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== All 2024+ Filings for CAA Committees ===')
            print(get_text(result))

            # Get the distinct purposes (not just "SEE SCHEDULE G")
            sql3 = """
            SELECT
                e.expn_dscr AS purpose,
                COUNT(*) AS tx_count,
                ROUND(SUM(e.amount), 2) AS total
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            AND fx.xref_id IN ('1421884', '1462796')
            AND e.expn_dscr IS NOT NULL AND e.expn_dscr != '' AND e.expn_dscr != 'SEE SCHEDULE G'
            GROUP BY e.expn_dscr
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Hilltop Expense Purposes (excluding SEE SCHEDULE G) ===')
            print(get_text(result))

            # Now look at the "SEE SCHEDULE G" line items to see what they break down to
            # These are likely the big media buys
            sql4 = """
            SELECT
                fx.xref_id AS cmte_id,
                e.filing_id,
                e.payee_naml AS payee,
                e.amount,
                e.expn_dscr AS purpose,
                e.expn_date
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            AND fx.xref_id IN ('1421884', '1462796')
            AND e.expn_dscr = 'SEE SCHEDULE G'
            GROUP BY fx.xref_id, e.filing_id, e.payee_naml, e.amount, e.expn_dscr, e.expn_date
            ORDER BY e.amount DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== SEE SCHEDULE G Line Items (Big Ticket Items) ===')
            print(get_text(result))

            # Check if there are any SCHEDULE G detail records
            sql5 = """
            SELECT table_name FROM information_schema.tables 
            WHERE table_name LIKE '%schedule%' OR table_name LIKE '%g%'
            ORDER BY table_name;
            """
            result = await session.call_tool('run_sql', {'sql': sql5})
            print('\n=== Schedule G Related Tables ===')
            print(get_text(result))

asyncio.run(run())
