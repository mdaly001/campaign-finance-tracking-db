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
print("TUBBS FOR LIEUTENANT GOVERNOR 2026; FRIENDS OF (1479071)")
print("UNDERSTANDING THE DOUBLE-COUNTING: EDT vs EXP RECORDS")
print("=" * 80)

# Group records by filing_id to see the structure
cur.execute("""
SELECT
    e.filing_id,
    COUNT(*) as num_records,
    ROUND(SUM(e.amount), 2) as total_amount,
    MIN(e.expn_date)::date as first_date,
    MAX(e.expn_date)::date as last_date
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
GROUP BY e.filing_id
ORDER BY e.filing_id;
""")

filing_rows = cur.fetchall()
print(f"\nFiling IDs with expenditures:")
for row in filing_rows:
    print(f"  Filing {row[0]}: {row[1]} records, ${float(row[2]):>12,.2f}, {row[3]} to {row[4]}")

# Now let's understand the EDT vs EXP structure
print()
print("=" * 80)
print("ANALYZING RECORD TYPES")
print("=" * 80)

# Check the rec_type values
cur.execute("""
SELECT 
    rec_type,
    COUNT(*) as num_records,
    ROUND(SUM(amount), 2) as total_amount
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
GROUP BY rec_type
ORDER BY total_amount DESC;
""")

print(f"\nBy rec_type:")
for row in cur.fetchall():
    print(f"  {row[0]:>10}: {row[1]:>5} records, ${float(row[2]):>12,.2f}")

# Now let's look at the TV ad pattern more carefully
print()
print("=" * 80)
print("THE RED7E TV AD BUY PATTERN")
print("=" * 80)

# Get the EDT records (the TV station payees with RED7E as agent)
cur.execute("""
SELECT
    e.filing_id,
    e.line_item,
    e.payee_naml,
    e.amount,
    e.expn_date,
    e.agent_naml
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
AND e.agent_naml = 'RED7E, INC.'
ORDER BY e.expn_date, e.payee_naml;
""")

tv_records = cur.fetchall()
print(f"\nTV station records (payee=TV station, agent=RED7E): {len(tv_records)} records")
print(f"Total from TV stations: ${sum(float(r[3] or 0) for r in tv_records):>14,.2f}")

print()
for row in tv_records:
    print(f"  {row[0]} L{row[1]}: {row[2]:<15} ${float(row[3] or 0):>12,.2f} ({row[4]}) [Agent: RED7E, INC.]")

# Get the EXP records for RED7E
cur.execute("""
SELECT
    e.filing_id,
    e.line_item,
    e.amount,
    e.expn_date,
    e.expn_dscr
FROM expn_cd_deduped e
WHERE e.filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND e.expn_date >= '2024-01-01' AND e.expn_date < '2027-01-01'
AND e.payee_naml = 'RED7E, INC.'
ORDER BY e.expn_date;
""")

red7e_records = cur.fetchall()
print(f"\nRED7E, INC. payee records: {len(red7e_records)} records")
print(f"Total to RED7E: ${sum(float(r[2] or 0) for r in red7e_records):>14,.2f}")

print()
for row in red7e_records:
    print(f"  {row[0]} L{row[1]}: ${float(row[2] or 0):>12,.2f} ({row[4]})")

# Calculate the "true" total by excluding the TV station records (which are downstream payments)
print()
print("=" * 80)
print("THE CORRECT TOTAL (excluding downstream TV station records)")
print("=" * 80)

cur.execute("""
SELECT 
    ROUND(SUM(amount), 2) as total_excl_tv
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
AND agent_naml IS NULL;  -- Only records where we don't have an agent (i.e., direct expenditures)
""")

row = cur.fetchone()
print(f"\nTotal (direct expenditures only, no agents): ${float(row[0] or 0):>14,.2f}")

# Also calculate total by excluding RED7E downstream
cur.execute("""
SELECT 
    ROUND(SUM(amount), 2) as total_excl_tv2
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
AND (agent_naml IS NULL OR agent_naml != 'RED7E, INC.');  -- Only records where we don't have RED7E as agent
""")

row = cur.fetchone()
print(f"Total (excluding RED7E agent records): ${float(row[0] or 0):>14,.2f}")

# What about Tubbs payments?
cur.execute("""
SELECT 
    ROUND(SUM(amount), 2) as tubbs_total
FROM expn_cd_deduped
WHERE filing_id IN (
    SELECT ff.filing_id FROM filer_filings_cd ff WHERE ff.filer_id = 1479071
)
AND expn_date >= '2024-01-01' AND expn_date < '2027-01-01'
AND payee_naml = 'TUBBS' AND payee_namf = 'MICHAEL';
""")

row = cur.fetchone()
print(f"Total paid to TUBBS, MICHAEL: ${float(row[0] or 0):>14,.2f}")

conn.close()
