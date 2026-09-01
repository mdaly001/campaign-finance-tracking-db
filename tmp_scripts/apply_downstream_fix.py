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

# Apply the downstream filter fix
print("Applying downstream filter to expn_cd_deduped...")

# Drop and recreate the view with the filter
cur.execute("DROP VIEW IF EXISTS expn_cd_deduped CASCADE;")
cur.execute("""
CREATE VIEW expn_cd_deduped AS
SELECT DISTINCT ON (filing_id, line_item)
    *
FROM expn_cd
WHERE agent_naml IS NULL
ORDER BY filing_id, line_item, amend_id DESC;
""")
print("✓ expn_cd_deduped view updated with downstream filter")

# Also update expn_all to use the new deduped view
cur.execute("DROP VIEW IF EXISTS expn_all CASCADE;")
cur.execute("""
CREATE VIEW expn_all AS
SELECT
    'expn_cd'    AS src,
    filing_id,
    tran_id,
    expn_date    AS expense_date,
    amount,
    COALESCE(
        NULLIF(payee_naml, ''),
        NULLIF(cmte_id, '')
    ) AS payee_key,
    payee_naml   AS payee_naml,
    payee_namf   AS payee_namf,
    expn_dscr    AS purpose,
    cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM expn_date)::INTEGER AS cycle
FROM expn_cd_deduped

UNION ALL

SELECT
    'lexp_cd'    AS src,
    filing_id,
    tran_id,
    expn_date    AS expense_date,
    amount,
    NULL         AS payee_key,
    payee_naml   AS payee_naml,
    payee_namf   AS payee_namf,
    expn_dscr    AS purpose,
    NULL         AS cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM expn_date)::INTEGER AS cycle
FROM lexp_cd_deduped

UNION ALL

SELECT
    's496_cd'    AS src,
    filing_id,
    tran_id,
    exp_date     AS expense_date,
    amount,
    NULL         AS payee_key,
    NULL         AS payee_naml,
    NULL         AS payee_namf,
    expn_dscr    AS purpose,
    NULL         AS cmte_id,
    memo_refno,
    EXTRACT(YEAR FROM exp_date)::INTEGER AS cycle
FROM s496_cd_deduped;
""")
print("✓ expn_all view updated")

# Verify the fix
print("\n" + "=" * 80)
print("VERIFICATION")
print("=" * 80)

cur.execute("""
SELECT COUNT(*), ROUND(SUM(amount), 2)
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
""")
rows = cur.fetchall()
print(f"\nTUBBS FRIENDS OF (1479071) after fix:")
print(f"  Records: {rows[0][0]}")
print(f"  Total: ${float(rows[0][1]):,.2f}")

# Also check Tubbs Michael
cur.execute("""
SELECT COUNT(*), ROUND(SUM(amount), 2)
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1471234
)
""")
rows = cur.fetchall()
print(f"\nTUBBS MICHAEL (1471234) after fix:")
print(f"  Records: {rows[0][0]}")
print(f"  Total: ${float(rows[0][1]):,.2f}")

# Global check
cur.execute("SELECT COUNT(*) FROM expn_cd_deduped;")
global_count = cur.fetchone()
print(f"\nGlobal expn_cd_deduped records: {global_count[0]}")

cur.execute("SELECT COUNT(*) FROM expn_cd;")
raw_count = cur.fetchone()
print(f"Global expn_cd raw records: {raw_count[0]}")

print(f"\nFiltered out: {raw_count[0] - global_count[0]} downstream/agent records")

conn.close()
