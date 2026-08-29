#!/usr/bin/env python3
"""Seed an admin API key + enable API-key auth in a fresh Open WebUI (WEBUI_AUTH=False).
Run inside cfdb-chat:  python3 owui_seed_key.py   -> prints the key (stable across runs).
NOTE: Open WebUI treats bearer tokens beginning with 'sk-' as API keys, so the
generated key MUST keep the 'sk-cfdb-' prefix."""
import json
import secrets
import sqlite3
import sys
import time

DB = "/app/backend/data/webui.db"
con = sqlite3.connect(DB)
now = int(time.time())

user = con.execute("select id from user where role='admin' order by created_at limit 1").fetchone()
if not user:
    print("NO_ADMIN_USER", file=sys.stderr)
    raise SystemExit(1)
uid = user[0]

row = con.execute("select key from api_key where user_id=? and id like 'cfdb-bootstrap%'", (uid,)).fetchone()
if row:
    con.execute("insert or replace into config(key, value, updated_at) values ('auth.enable_api_keys', 'true', ?)", (now,))
    con.commit()
    print(row[0])
    raise SystemExit(0)

key = "sk-cfdb-" + secrets.token_hex(16)
con.execute(
    "insert into api_key(id, user_id, key, data, created_at, updated_at) values (?,?,?,?,?,?)",
    ("cfdb-bootstrap-" + secrets.token_hex(4), uid, key, json.dumps({"name": "cfdb-installer"}), now, now),
)
con.execute("insert or replace into config(key, value, updated_at) values ('auth.enable_api_keys', 'true', ?)", (now,))
con.commit()
print(key)
