# Credential rotation — `cfdb_reader` and the `cfdb` superuser

## Why

Historically the read-only MCP role was created with a hardcoded literal —
a `CREATE ROLE cfdb_reader LOGIN PASSWORD '<same-as-username>'` statement
(the password equalled the role name).

That literal lived in `migrations/0001_create_all_tables.sql` and in the
compose files, so a known credential was committed to the repository and a
fresh install shipped with a guessable read-only password. This is exactly
what a secret scanner (GitGuardian) flags.

The fix removes every literal. The role password is now supplied at deploy
time and there is **no default** — a missing value fails loudly instead of
silently creating a known-password role.

## How the password reaches the role (fresh install)

`migrations/0001_create_all_tables.sql` reads a custom Postgres GUC. The
NULL/empty check is deliberately **inside** the `NOT EXISTS` guard so that
re-applying this migration on a cluster where the role already exists is a
no-op (the runner re-applies by file-hash over a connection that carries
no `PGOPTIONS`):

```sql
DO $$
DECLARE
    reader_pw text := current_setting('cfdb.reader_password', true);
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cfdb_reader') THEN
        IF reader_pw IS NULL OR reader_pw = '' THEN
            RAISE EXCEPTION 'cfdb.reader_password is not set. ...';
        END IF;
        EXECUTE format('CREATE ROLE cfdb_reader LOGIN PASSWORD %L', reader_pw);
    END IF;
END
$$;
```

The compose file injects that GUC into the Postgres server via `PGOPTIONS`:

```yaml
PGOPTIONS: -c cfdb.reader_password=${CFDB_READER_PASSWORD:?...}
```

`PGOPTIONS` is inherited by the `psql` the image entrypoint runs during
first-boot initdb, so the role is created with the environment-supplied
password. Verified: the stored `pg_authid.rolpassword` is a real SCRAM
verifier derived from the env value, not a literal.

## Required environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `DB_PASSWORD` | prod compose | `cfdb` superuser password |
| `CFDB_READER_PASSWORD` | prod + testing compose | `cfdb_reader` read-only role password |
| `TEST_DB_PASSWORD` | testing compose | testing `cfdb` superuser password |

Copy `.env.example` → `.env` (prod) and `.env.testing.example` →
`.env.testing` (testing). Both real files are gitignored.

## Rotating an EXISTING cluster

The migration block is idempotent (`IF NOT EXISTS`) and will **not**
overwrite an existing role. To change the password on a cluster that is
already initialised:

```bash
# 1. Pick a new strong password and put it in your .env / .env.testing.

# 2. Apply it to the running cluster:
docker exec -it <db-container> psql -U cfdb -d cfdb \
  -c "ALTER ROLE cfdb_reader PASSWORD '<new-value>';"

# 3. Restart the MCP service so it picks up the new DATABASE_URL:
docker compose up -d --force-recreate mcp
```

The same applies to the `cfdb` superuser (`ALTER ROLE cfdb PASSWORD ...`),
though that role is not used by the MCP server.

### The copied-testing-cluster wrinkle

The testing cluster is a `pg_basebackup` copy of Prod, so it already
contains `cfdb_reader` with whatever password Prod's had. The migration
does not re-run on it. Therefore, for the testing stack,
`CFDB_READER_PASSWORD` in `.env.testing` **must match the copied
cluster's current role password**, or you must `ALTER ROLE` the copied
cluster to the new value first (step 2 above, against `cfdbtest-db`).

## Verifying a rotation

```bash
# Correct password connects:
PGPASSWORD='<new>' psql -h <host> -p <port> -U cfdb_reader -d cfdb -c 'select 1;'
# Wrong password is rejected:
PGPASSWORD='wrong' psql -h <host> -p <port> -U cfdb_reader -d cfdb -c 'select 1;'
# -> FATAL: password authentication failed
```

Note: connections over the **local unix socket** inside the db container use
`trust` auth (Postgres default) and will not exercise the password. Test
over TCP (`-h 127.0.0.1` from another container, or the published host
port) to actually verify authentication.

## What this does NOT do

- It does not rotate the live Prod cluster by itself — that is the
  operational `ALTER ROLE` step above, performed deliberately.
- It does not change the `cfdb` superuser's role in the app (the MCP server
  only ever uses `cfdb_reader`).
