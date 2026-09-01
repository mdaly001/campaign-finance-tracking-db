import psycopg2, os

env = {}
for line in open('.env'):
    if '=' in line:
        k, v = line.strip().split('=', 1)
        env[k] = v

conn = psycopg2.connect(
    host=env['DB_HOST'],
    port=int(env['DB_PORT']),
    dbname=env['DB_NAME'],
    user=env['DB_USER'],
    password=env['DB_PASSWORD']
)
conn.autocommit = True
cur = conn.cursor()

print("=" * 80)
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; FRIENDS OF")
print("Filer ID: 1479071 - DETAILED EXPENDITURE RECORDS")
print("=" * 80)

# Get all expenditure records with proper column mapping
cur.execute("""
SELECT
    e.filing_id,
    e.amend_id,
    e.line_item,
    e.payee_naml,
    e.payee_namf,
    e.payee_namt,
    e.payee_nams,
    e.expn_dscr,
    e.amount,
    e.expn_date,
    e.tran_id,
    e.agent_naml,
    e.agent_namf,
    e.agent_namt,
    e.agent_nams
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
ORDER BY e.expn_date, e.filing_id, e.line_item;
""")

rows = cur.fetchall()
print(f"\nTotal deduped records: {len(rows)}")
print()
print(f"{'Filing':>12} {'Line':>4} {'Tran':<15} {'Payee':<35} {'Agent':<30} {'Date':<12} {'Amount':>14} {'Description'}")
print("-" * 140)

for row in rows:
    filing_id = row[0]
    amend_id = row[1]
    line_item = row[2]
    payee = f"{str(row[3])} {str(row[4])} {str(row[5])} {str(row[6])}".strip()[:35]
    agent = f"{str(row[11])} {str(row[12])} {str(row[13])} {str(row[14])}".strip()[:30]
    expn_dscr = str(row[7]) if row[7] else ''
    amount = float(row[8] or 0)
    expn_date = str(row[9])[:10] if row[9] else ''
    tran_id = str(row[10])[:15]
    
    print(f"{filing_id:>12} {line_item:>4} {tran_id:<15} {payee:<35} {agent:<30} {expn_date:<12} ${amount:>12,.2f} {expn_dscr[:40]}")

print("\n" + "=" * 80)
print("GROUPING BY PAYEE TO CHECK FOR DUPLICATES")
print("=" * 80)

# Group by payee to see all records
cur.execute("""
SELECT
    payee_naml,
    payee_namf,
    payee_namt,
    payee_nams,
    COUNT(*) as num_records,
    ROUND(SUM(amount), 2) as total_amount,
    MIN(expn_date)::date as first_date,
    MAX(expn_date)::date as last_date,
    array_agg(DISTINCT expn_dscr) as descriptions
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
GROUP BY payee_naml, payee_namf, payee_namt, payee_nams
ORDER BY total_amount DESC;
""")

rows = cur.fetchall()
print(f"\nUnique payees: {len(rows)}")
print()
for row in rows:
    payee = f"{str(row[0])} {str(row[1])} {str(row[2])} {str(row[3])}".strip()[:35]
    num_records = row[4]
    total = float(row[5])
    first = row[6]
    last = row[7]
    desc_count = len(row[8]) if row[8] else 0
    
    # Get unique descriptions
    cur.execute("""
    SELECT DISTINCT expn_dscr FROM expn_cd_deduped
    WHERE filing_id IN (SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071)
    AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
    AND payee_naml = %s AND payee_namf = %s AND payee_namt = %s
    ORDER BY expn_dscr;
    """, (row[0], row[1], row[2]))
    
    descs = [r[0] for r in cur.fetchall() if r[0]]
    
    print(f"Payee: {payee}")
    print(f"  Records: {num_records}, Total: ${total:>14,.2f}, Date range: {first} to {last}")
    print(f"  Descriptions: {len(descs)} unique")
    for d in descs[:5]:  # Show first 5 descriptions
        print(f"    - {d}")
    if len(descs) > 5:
        print(f"    ... and {len(descs) - 5} more")
    print()

conn.close()
