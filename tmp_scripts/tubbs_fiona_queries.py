import psycopg2, os, decimal

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

def fmt(row):
    name = str(row[0]).strip()
    count = int(row[1])
    amount = float(row[2])
    return f"{name:<70s} | ${amount:>14,.2f} | {count} contributions"

print("=" * 80)
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; FRIENDS OF")
print("Filer ID: 1479071")
print("=" * 80)

cur.execute("""
SELECT
    TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')) AS donor_name,
    COUNT(*) AS num_contributions,
    ROUND(SUM(r.amount), 2) AS total_given
FROM rcpt_cd_deduped r
WHERE r.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND r.rcpt_date >= '2024-01-01' AND r.rcpt_date < '2027-01-01'
GROUP BY r.ctrib_naml, r.ctrib_namf, r.ctrib_namt
ORDER BY total_given DESC
LIMIT 20;
""")

rows = cur.fetchall()
total = 0.0
for row in rows:
    print(fmt(row))
    total += float(row[2])

print("-" * 80)
print(f"TOTAL: ${total:>14,.2f}")

print()
print("=" * 80)
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; MICHAEL")
print("Filer ID: 1471234")
print("=" * 80)

cur.execute("""
SELECT
    TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')) AS donor_name,
    COUNT(*) AS num_contributions,
    ROUND(SUM(r.amount), 2) AS total_given
FROM rcpt_cd_deduped r
WHERE r.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1471234
)
AND r.rcpt_date >= '2024-01-01' AND r.rcpt_date < '2027-01-01'
GROUP BY r.ctrib_naml, r.ctrib_namf, r.ctrib_namt
ORDER BY total_given DESC
LIMIT 20;
""")

rows = cur.fetchall()
total = 0.0
for row in rows:
    print(fmt(row))
    total += float(row[2])

print("-" * 80)
print(f"TOTAL: ${total:>14,.2f}")

print()
print("=" * 80)
print("NO ON FIONA MA")
print("=" * 80)

cur.execute("""
SELECT
    ff.filer_id,
    TRIM(COALESCE(n.naml, '') || ' ' || COALESCE(n.namf, '')) AS committee_name,
    COUNT(DISTINCT r.filing_id) AS donations_2024,
    ROUND(COALESCE(SUM(r.amount), 0), 2) AS total_2024
FROM filer_filings_cd ff
JOIN filername_cd n ON ff.filer_id = n.filer_id
LEFT JOIN rcpt_cd_deduped r ON r.filing_id = ff.filing_id
    AND r.rcpt_date >= '2024-01-01' AND r.rcpt_date < '2025-01-01'
WHERE (TRIM(COALESCE(n.naml, '') || ' ' || COALESCE(n.namf, '')) ILIKE '%No on Fiona%'
    OR TRIM(COALESCE(n.naml, '') || ' ' || COALESCE(n.namf, '')) ILIKE '%AGAINST FIONA%')
GROUP BY ff.filer_id, n.naml, n.namf;
""")

print()
for row in cur.fetchall():
    print(f"Filer {row[0]}: {row[1]}")
    print(f"  2024 donations: {row[2]}")
    print(f"  2024 total: ${float(row[3]):,.2f}")

conn.close()
