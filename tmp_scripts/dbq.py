"""Ad-hoc query helper against the remote cfdb. Usage: python dbq.py "SQL" [--csv]"""
import sys, psycopg2, os

env = {}
for line in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        env[k] = v

conn = psycopg2.connect(host=env["DB_HOST"], port=int(env["DB_PORT"]), user=env["DB_USER"],
                       password=env["DB_PASSWORD"], dbname=env["DB_NAME"], connect_timeout=10)
conn.autocommit = True
cur = conn.cursor()
sql = sys.argv[1]
cur.execute("SET statement_timeout = '280s'")
cur.execute(sql)
if cur.description:
    cols = [d[0] for d in cur.description]
    print("\t".join(cols))
    for row in cur.fetchall():
        print("\t".join("" if v is None else str(v) for v in row))
conn.close()
