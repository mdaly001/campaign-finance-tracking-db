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

# Test the downstream filter approach
print("=" * 80)
print("TESTING: EXCLUDING DOWNSTREAM RECORDS (agent_naml IS NOT NULL)")
print("=" * 80)

# Current total (with downstream)
cur.execute("""
SELECT COUNT(*), ROUND(SUM(amount), 2)
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
""")
rows = cur.fetchall()
print(f"\nCurrent total (all records): {rows[0][0]} records, ${float(rows[0][1]):,.2f}")

# Correct total (excluding downstream)
cur.execute("""
SELECT COUNT(*), ROUND(SUM(amount), 2)
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND agent_naml IS NULL
""")
rows = cur.fetchall()
print(f"Correct total (no downstream): {rows[0][0]} records, ${float(rows[0][1]):,.2f}")

# Verify downstream breakdown
cur.execute("""
SELECT agent_naml, COUNT(*), ROUND(SUM(amount), 2)
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND agent_naml IS NOT NULL
GROUP BY agent_naml
ORDER BY SUM(amount) DESC
""")
print("\nDownstream records by agent:")
for row in cur.fetchall():
    print(f"  {row[0]:<30} {row[1]:>5} records, ${float(row[2]):>12,.2f}")

# Show what the downstream records look like
print("\nDownstream detail records:")
cur.execute("""
SELECT payee_naml, payee_namf, amount, expn_dscr
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND agent_naml IS NOT NULL
ORDER BY amount DESC
""")
for row in cur.fetchall():
    payee = f"{row[0]} {row[1]}".strip()[:30]
    print(f"  {payee:<30} ${float(row[2] or 0):>12,.2f}  ({str(row[3])[:30]})")

conn.close()
