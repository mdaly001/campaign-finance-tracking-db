import psycopg2, os
from collections import defaultdict

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
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; FRIENDS OF (1479071)")
print("UNDERSTANDING THE DOUBLE-COUNTING: DOWNSTREAM EXPENDITURES")
print("=" * 80)

# Get ALL records with their structure
cur.execute("""
SELECT
    e.filing_id,
    e.line_item,
    e.payee_naml,
    e.payee_namf,
    e.amount,
    e.expn_date,
    e.expn_dscr,
    e.agent_naml
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
ORDER BY e.expn_date, e.filing_id, e.line_item;
""")

all_records = cur.fetchall()

# Group by filing_id
filing_groups = defaultdict(list)
for row in all_records:
    filing_groups[row[0]].append(row)

# Now let's analyze each filing
print("\n" + "=" * 80)
print("DETAILED FILING ANALYSIS")
print("=" * 80)

for filing_id in sorted(filing_groups.keys()):
    recs = filing_groups[filing_id]
    print(f"\n{'='*80}")
    print(f"FILING ID: {filing_id} ({len(recs)} records)")
    print("="*80)
    
    # Show all records
    for rec in recs:
        line_item = rec[1]
        payee = f"{rec[2]} {rec[3]}".strip()[:25]
        amount = float(rec[4] or 0)
        date = str(rec[5])[:10] if rec[5] else ''
        desc = f"{rec[6]}".strip()[:30] if rec[6] else ''
        agent = f"{rec[7]}".strip()[:15] if rec[7] else ''
        
        marker = ""
        if agent == "RED7E, INC.":
            marker = " <-- DOWNSTREAM (agent=RED7E)"
        elif payee == "RED7E, INC.":
            marker = " <-- UPSTREAM (payee=RED7E)"
        
        print(f"  L{line_item:>4}: {payee:<25} ${amount:>12,.2f} [{agent:<15}] {desc}{marker}")
    
    # Calculate totals for this filing
    total_all = sum(float(r[4] or 0) for r in recs)
    total_upstream = sum(float(r[4] or 0) for r in recs if r[7] != "RED7E, INC.")
    total_downstream = sum(float(r[4] or 0) for r in recs if r[7] == "RED7E, INC.")
    
    print(f"\n  TOTAL (all):      ${total_all:>12,.2f}")
    print(f"  TOTAL (upstream): ${total_upstream:>12,.2f}")
    print(f"  TOTAL (downstream): ${total_downstream:>12,.2f}")
    print(f"  DIFFERENCE:       ${total_all - total_upstream:>12,.2f}")

# Overall summary
print("\n" + "=" * 80)
print("OVERALL SUMMARY")
print("=" * 80)

total_all = sum(float(r[4] or 0) for r in all_records)
total_upstream = sum(float(r[4] or 0) for r in all_records if r[7] != "RED7E, INC.")
total_downstream = sum(float(r[4] or 0) for r in all_records if r[7] == "RED7E, INC.")

print(f"\nTotal expenditures (all records):     ${total_all:>14,.2f}")
print(f"Total expenditures (upstream only):   ${total_upstream:>14,.2f}")
print(f"Total expenditures (downstream only): ${total_downstream:>14,.2f}")
print(f"\nThe 'downstream' records are RED7E's purchases on behalf of the committee.")
print(f"Counting both upstream AND downstream double-counts the same money.")
print(f"\nThe CORRECT total is: ${total_upstream:>14,.2f}")

# Show the downstream records separately
print("\n" + "=" * 80)
print("DOWNSTREAM RECORDS (RED7E as agent)")
print("=" * 80)

cur.execute("""
SELECT
    e.filing_id,
    e.line_item,
    e.payee_naml,
    e.amount,
    e.expn_date,
    e.expn_dscr
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
AND e.agent_naml = 'RED7E, INC.'
ORDER BY e.expn_date, e.payee_naml;
""")

downstream_records = cur.fetchall()
print(f"\nDownstream records: {len(downstream_records)}")
print(f"Total downstream: ${sum(float(r[3] or 0) for r in downstream_records):,.2f}")

for rec in downstream_records:
    print(f"  {rec[0]} L{rec[1]}: {rec[2]:<15} ${float(rec[3] or 0):>12,.2f} ({rec[4]})")

conn.close()
