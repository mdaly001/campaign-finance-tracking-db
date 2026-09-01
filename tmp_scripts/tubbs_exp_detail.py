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

# Get all expenditure records with full detail
cur.execute("""
SELECT
    e.filing_id,
    e.amend_id,
    e.line_item,
    e.payee_naml,
    e.payee_namf,
    e.payee_namt,
    e.expn_dscr,
    e.amount,
    e.expn_date,
    e.tran_id,
    e.agent_naml,
    e.agent_namf,
    e.agent_namt
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
ORDER BY e.filing_id, e.line_item;
""")

rows = cur.fetchall()
print(f"\nTotal deduped records: {len(rows)}")
print(f"\n{'Filing ID':>12} {'Line':>5} {'Amend':>5} {'Tran ID':<20} {'Payee':<35} {'Agent':<25} {'Amount':>14} {'Date':<12} {'Description'}")
print("-" * 160)

for row in rows:
    filing_id = row[0]
    line_item = row[1]
    amend_id = row[2]
    tran_id = str(row[9])[:20]
    payee = f"{str(row[3])} {str(row[4])} {str(row[5])}".strip()[:35]
    agent = f"{str(row[10])} {str(row[11])} {str(row[12])}".strip()[:25]
    amount = row[6]
    expn_date = str(row[7])[:10] if row[7] else ''
    expn_dscr = str(row[7]).strip()[:30] if row[7] else ''
    
    # Get description - it's row[7] in the query which is expn_dscr
    expn_dscr = row[7] if row[7] else ''
    
    print(f"{filing_id:>12} {line_item:>5} {amend_id:>5} {tran_id:<20} {payee:<35} {agent:<25} ${float(amount or 0):>12,.2f} {expn_date:<12} {str(expn_dscr)[:50]}")

print("\n" + "=" * 80)
print("CHECKING FOR DUPLICATE FILING_IDS AND TRAN_IDs")
print("=" * 80)

# Check for duplicate filing_ids
cur.execute("""
SELECT 
    filing_id, 
    COUNT(*) as num_records,
    SUM(amount) as total_amount,
    COUNT(DISTINCT tran_id) as unique_tran_ids
FROM expn_cd_deduped 
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
GROUP BY filing_id
HAVING COUNT(*) > 1
ORDER BY filing_id;
""")

dup_rows = cur.fetchall()
print(f"\nFiling IDs with multiple records: {len(dup_rows)}")
for row in dup_rows[:20]:
    print(f"  Filing {row[0]}: {row[1]} records, ${float(row[2]):,.2f}, {row[3]} unique tran_ids")

# Check for duplicate tran_ids across all records
cur.execute("""
SELECT 
    tran_id,
    COUNT(*) as num_records,
    SUM(amount) as total_amount,
    COUNT(DISTINCT filing_id) as num_filings
FROM expn_cd_deduped 
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
GROUP BY tran_id
HAVING COUNT(*) > 1
ORDER BY num_records DESC;
""")

tran_dupes = cur.fetchall()
print(f"\nTran IDs with multiple records: {len(tran_dupes)}")
for row in tran_dupes[:20]:
    print(f"  Tran {row[0]}: {row[1]} records, ${float(row[2]):,.2f}, {row[3]} filings")

conn.close()
