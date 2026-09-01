import psycopg2, os

env = {}
for line in open('.env'):
    if '=' in line:
        k, v = line.strip(). split('=', 1)
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
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; FRIENDS OF (1479071)")
print("UNDERSTANDING THE DOUBLE-COUNTING")
print("=" * 80)

# Get all records grouped by payee and agent
cur.execute("""
SELECT
    CASE 
        WHEN agent_naml IS NOT NULL THEN 'AGENT: ' || agent_naml
        ELSE 'DIRECT'
    END as payment_type,
    payee_naml,
    payee_namf,
    payee_namt,
    COUNT(*) as num_records,
    ROUND(SUM(amount), 2) as total_amount,
    MIN(expn_date)::date as first_date,
    MAX(expn_date)::date as last_date
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
GROUP BY agent_naml, payee_naml, payee_namf, payee_namt
ORDER BY total_amount DESC;
""")

print(f"\n{'Payment Type':<20} {'Payee':<25} {'Records':>7} {'Total':>14} {'Date Range'}")
print("-" * 100)

for row in cur.fetchall():
    payment_type = row[0]
    payee = f"{row[1]} {row[2]} {row[3]}".strip()[:25]
    num_records = row[4]
    total = float(row[5])
    date_range = f"{row[6]} to {row[7]}"
    
    print(f"{payment_type:<20} {payee:<25} {num_records:>7} ${total:>12,.2f} {date_range}")

# Now let's check if there are duplicate filing_ids with same amount
print()
print("=" * 80)
print("CHECKING FOR DUPLICATE RECORDS (same filing_id + same amount)")
print("=" * 80)

cur.execute("""
SELECT
    filing_id,
    line_item,
    payee_naml,
    payee_namf,
    amount,
    expn_dscr,
    agent_naml
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
ORDER BY filing_id, line_item;
""")

records = cur.fetchall()
print(f"\nTotal deduped records: {len(records)}")

# Group by filing_id and check for duplicate amounts
from collections import defaultdict
filing_groups = defaultdict(list)
for row in records:
    filing_id = row[0]
    filing_groups[filing_id].append(row)

print(f"\nFiling IDs: {len(filing_groups)}")
for filing_id, recs in filing_groups.items():
    print(f"\nFiling {filing_id}: {len(recs)} records")
    for rec in recs[:10]:  # Show first 10 records
        payee = f"{rec[2]} {rec[3]}".strip()[:25]
        agent = f"{rec[6]}".strip()[:15] if rec[6] else ''
        desc = f"{rec[5]}".strip()[:30] if rec[5] else ''
        print(f"  L{rec[1]:>4}: {payee:<25} ${float(rec[4] or 0):>12,.2f} [{agent:<15}] {desc}")
    if len(recs) > 10:
        print(f"  ... and {len(recs) - 10} more")

# Check for same amount in same filing
print()
print("=" * 80)
print("SAME AMOUNT RECORDS IN SAME FILING (potential duplicates)")
print("=" * 80)

for filing_id, recs in filing_groups.items():
    amounts = [float(r[4] or 0) for r in recs]
    if len(amounts) != len(set(amounts)):
        print(f"\nFiling {filing_id} has duplicate amounts:")
        from collections import Counter
        amount_counts = Counter(amounts)
        for amount, count in amount_counts.items():
            if count > 1:
                print(f"  ${amount:>12,.2f}: {count} times")

conn.close()
