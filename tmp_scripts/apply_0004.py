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

with open('migrations/0004_dedup_views.sql') as f:
    sql = f.read()

print('Applying migration 0004...')
cur.execute(sql)
print('Migration applied successfully!')

# Verify row counts
tables = ['rcpt_cd', 'rcpt_cd_deduped', 'expn_cd', 'expn_cd_deduped',
          's497_cd', 's497_cd_deduped', 's496_cd', 's496_cd_deduped',
          's498_cd', 's498_cd_deduped']
for t in tables:
    cur.execute(f'SELECT COUNT(*) FROM {t}')
    print(f'  {t}: {cur.fetchone()[0]:,} rows')

# Verify Steve Phillips on deduped data
cur.execute("""
SELECT 
    COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '') AS donor_name,
    COUNT(*) AS num_contributions,
    ROUND(SUM(r.amount), 2) AS total_given
FROM rcpt_cd_deduped r
WHERE r.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND (r.ctrib_naml ILIKE '%Phillips%' OR r.ctrib_namf ILIKE '%Phillips%')
GROUP BY r.ctrib_naml, r.ctrib_namf, r.ctrib_namt
ORDER BY total_given DESC;
""")
print('\nSteve Phillips on deduped data:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[2]:,.2f} ({row[1]} contributions)')

# Verify Tubbs Friends OF committee on deduped data
cur.execute("""
SELECT
    TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')) AS donor_name,
    COUNT(*) AS num_contributions,
    ROUND(SUM(r.amount), 2) AS total_given
FROM rcpt_cd_deduped r
WHERE r.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND r.rcpt_date >= '2025-01-01' AND r.rcpt_date < '2026-12-31'
GROUP BY r.ctrib_naml, r.ctrib_namf, r.ctrib_namt
ORDER BY total_given DESC
LIMIT 15;
""")
print('\nTubbs Friends OF (deduped) top 15 donors:')
for row in cur.fetchall():
    print(f'  {row[0]}: ${row[2]:,.2f} ({row[1]} contributions)')

# Verify Tubbs Michael committee on deduped data
cur.execute("""
SELECT
    TRIM(COALESCE(r.ctrib_naml, '') || ' ' || COALESCE(r.ctrib_namf, '')) AS donor_name,
    COUNT(*) AS num_contributions,
    ROUND(SUM(r.amount), 2) AS total_given
FROM rcpt_cd_deduped r
WHERE r.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1471234
)
AND r.rcpt_date >= '2025-01-01' AND r.rcpt_date < '2026-12-31'
GROUP BY r.ctrib_naml, r.ctrib_namf, r.ctrib_namt
ORDER BY total_given DESC
LIMIT 15;
""")
print('\nTubbs Michael (deduped) top 15 donors:')
for row in cur.fetchall():
    print(f'  {row[0]}: ${row[2]:,.2f} ({row[1]} contributions)')

conn.close()
