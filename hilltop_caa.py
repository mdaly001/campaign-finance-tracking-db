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

            # Get all Hilltop expenditures for the 3 CA Apartment Assoc. committees
            # 1421884 = Californians for Responsible Housing
            # 1462796 = Protect Patients Now / Yes on 34
            # 1459171 = Protect California Schools (sponsored by Building a Better California)
            
            # Query 1: All Hilltop expenses for CA Apartment Assoc. committees, with purpose breakdown
            sql1 = """
            SELECT
                fn.naml AS committee_name,
                fx.xref_id AS cmte_id,
                e.expn_dscr AS purpose,
                e.payee_naml AS payee,
                COUNT(*) AS tx_count,
                ROUND(SUM(e.amount), 2) AS total,
                ROUND(AVG(e.amount), 2) AS avg_amount
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            AND fx.xref_id IN ('1421884', '1462796')
            AND e.expn_dscr IS NOT NULL AND e.expn_dscr != ''
            GROUP BY fn.naml, fx.xref_id, e.expn_dscr, e.payee_naml
            ORDER BY total DESC
            LIMIT 40;
            """
            result = await session.call_tool('run_sql', {'sql': sql1})
            print('=== CA Apartment Assoc. Hilltop Expenses by Purpose ===')
            print(get_text(result))
            
            # Query 2: Yearly breakdown per committee
            sql2 = """
            SELECT
                fx.xref_id AS cmte_id,
                MAX(fn.naml) AS committee_name,
                EXTRACT(YEAR FROM e.expn_date)::INTEGER AS year,
                COUNT(*) AS tx_count,
                ROUND(SUM(e.amount), 2) AS total
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            AND fx.xref_id IN ('1421884', '1462796')
            GROUP BY fx.xref_id, year
            HAVING year IS NOT NULL
            ORDER BY fx.xref_id, year;
            """
            result = await session.call_tool('run_sql', {'sql': sql2})
            print('\n=== CA Apartment Assoc. Hilltop Spending by Year ===')
            print(get_text(result))
            
            # Query 3: Total Hilltop spending across all CA Apartment Assoc. related committees
            sql3 = """
            SELECT
                fx.xref_id AS cmte_id,
                MAX(fn.naml) AS committee_name,
                COUNT(DISTINCT e.filing_id) AS filing_ids,
                COUNT(*) AS tx_count,
                ROUND(SUM(e.amount), 2) AS total,
                MIN(EXTRACT(YEAR FROM e.expn_date)::INTEGER) AS earliest,
                MAX(EXTRACT(YEAR FROM e.expn_date)::INTEGER) AS latest
            FROM expn_cd e
            JOIN filer_filings_cd ff ON ff.filing_id = e.filing_id
            JOIN filer_xref_cd fx ON fx.filer_id = ff.filer_id
            JOIN filername_cd fn ON fn.filer_id = ff.filer_id
            WHERE e.payee_naml ILIKE '%hilltop%'
            AND fx.xref_id IN ('1421884', '1462796')
            GROUP BY fx.xref_id
            ORDER BY total DESC;
            """
            result = await session.call_tool('run_sql', {'sql': sql3})
            print('\n=== Total Hilltop Spending per CA Apartment Assoc. Committee ===')
            print(get_text(result))
            
            # Query 4: What were the ballot measures? Get filing details for the big 2024 filings
            sql4 = """
            SELECT
                filing_id,
                filing_date,
                form_type
            FROM filer_filings_cd
            WHERE filing_id IN (
                SELECT DISTINCT filing_id FROM expn_cd 
                WHERE payee_naml ILIKE '%hilltop%'
                AND filing_id IN (
                    SELECT filing_id FROM filer_filings_cd 
                    WHERE filer_id IN (
                        SELECT filer_id FROM filer_xref_cd 
                        WHERE xref_id IN ('1421884', '1462796')
                    )
                )
            )
            AND filing_date >= '2024-01-01'
            AND filing_date < '2025-01-01'
            ORDER BY filing_id;
            """
            result = await session.call_tool('run_sql', {'sql': sql4})
            print('\n=== 2024 Filings for CA Apartment Assoc. Committees ===')
            print(get_text(result))

asyncio.run(run())
