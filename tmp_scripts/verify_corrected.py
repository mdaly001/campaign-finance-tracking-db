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

# Test the corrected expenditure query
print("=" * 80)
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; FRIENDS OF")
print("Filer ID: 1479071 — CORRECTED EXPENDITURES")
print("=" * 80)

cur.execute("""
SELECT
    TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, '')) AS payee_name,
    COUNT(*) AS num_transactions,
    ROUND(SUM(e.amount), 2) AS total_spent
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
GROUP BY e.payee_naml, e.payee_namf, e.payee_namt
ORDER BY total_spent DESC
LIMIT 30;
""")

rows = cur.fetchall()
total = 0.0
for row in rows:
    name = str(row[0]).strip()
    count = int(row[1])
    amount = float(row[2])
    total += amount
    print(f"{name:<70s} | ${amount:>14,.2f} | {count} transactions")

print("-" * 80)
print(f"TOTAL EXPENDED: ${total:>14,.2f}")

# Also check Tubbs Michael
print()
print("=" * 80)
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; MICHAEL")
print("Filer ID: 1471234 — CORRECTED EXPENDITURES")
print("=" * 80)

cur.execute("""
SELECT
    TRIM(COALESCE(e.payee_naml, '') || ' ' || COALESCE(e.payee_namf, '')) AS payee_name,
    COUNT(*) AS num_transactions,
    ROUND(SUM(e.amount), 2) AS total_spent
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1471234
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
GROUP BY e.payee_naml, e.payee_namf, e.payee_namt
ORDER BY total_spent DESC
LIMIT 30;
""")

rows = cur.fetchall()
total = 0.0
for row in rows:
    name = str(row[0]).strip()
    count = int(row[1])
    amount = float(row[2])
    total += amount
    print(f"{name:<70s} | ${amount:>14,.2f} | {count} transactions")

print("-" * 80)
print(f"TOTAL EXPENDED: ${total:>14,.2f}")

conn.close()
