#!/usr/bin/env bash
# ============================================================================
# testing_env.sh — control the isolated CFDB testing stack.
#
# NEVER targets the Prod stack (project `prod`, cfdb-db / cfdb-mcp / :9527).
# Everything here runs under compose project `cfdbtest` with its own network,
# containers, image tag and data directory.
#
#   ./scripts/testing_env.sh up       build image + start db & mcp
#   ./scripts/testing_env.sh build    rebuild the cfdb-app:testing image only
#   ./scripts/testing_env.sh status   containers, ports, DB sanity
#   ./scripts/testing_env.sh psql     psql into the TESTING database
#   ./scripts/testing_env.sh test     run the pytest suite in the app container
#   ./scripts/testing_env.sh probe <tool> [json-args]   call one MCP tool
#   ./scripts/testing_env.sh down     stop the testing stack (keeps data)
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.testing.yml"
PROJECT="cfdbtest"
MCP_PORT="${TEST_MCP_PORT:-9528}"
DB_PORT="${TEST_DB_PORT:-5433}"

# Keep buildx state inside the workspace (~/.docker may be read-only here).
export BUILDX_CONFIG="$REPO_ROOT/.buildx"
mkdir -p "$BUILDX_CONFIG"

# -p pins the project so we can never accidentally address the Prod stack.
# --env-file loads the gitignored .env.testing (TEST_DB_PASSWORD,
# CFDB_READER_PASSWORD, ports, tag). If it's missing, compose fails loud on
# the required vars rather than falling back to a literal default.
ENV_FILE="$REPO_ROOT/.env.testing"
ENV_ARGS=()
if [ -f "$ENV_FILE" ]; then
  ENV_ARGS=(--env-file "$ENV_FILE")
fi
dc() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "${ENV_ARGS[@]}" "$@"; }

cmd="${1:-status}"
shift || true

case "$cmd" in
  build)
    echo "[testing] building cfdb-app:${TEST_IMAGE_TAG:-testing}"
    dc build mcp
    ;;

  up)
    echo "[testing] starting isolated stack (project=$PROJECT)"
    dc up -d db
    echo "[testing] waiting for db healthy"
    for i in $(seq 1 60); do
      st=$(docker inspect --format '{{.State.Health.Status}}' cfdbtest-db 2>/dev/null || echo starting)
      [ "$st" = healthy ] && break
      sleep 2
    done
    echo "[testing] db status: $st"
    dc up -d mcp
    echo "[testing] waiting for MCP on :$MCP_PORT"
    for i in $(seq 1 45); do
      if curl -s -o /dev/null -m 2 "http://127.0.0.1:$MCP_PORT/mcp"; then break; fi
      sleep 2
    done
    echo "[testing] mcp status: $(docker inspect --format '{{.State.Status}}' cfdbtest-mcp 2>/dev/null || echo unknown)"
    echo "[testing] MCP endpoint: http://127.0.0.1:$MCP_PORT/mcp"
    ;;

  status)
    echo "=== testing containers ==="
    docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo "=== prod containers (must be untouched) ==="
    docker ps -a --filter "label=com.docker.compose.project=prod" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo "=== testing db sanity ==="
    docker exec cfdbtest-db psql -U cfdb -d cfdb -t -c \
      "select 'expn_cd rows: '||count(*) from expn_cd;" 2>&1 || echo "(db not up)"
    docker exec cfdbtest-db psql -U cfdb -d cfdb -t -c \
      "select 'IEC 1489398 filings: '||count(*) from filer_filings_cd where filer_id=1489398;" 2>&1 || true
    ;;

  psql)
    exec docker exec -it cfdbtest-db psql -U cfdb -d cfdb
    ;;

  test)
    shift || true
    dc run --rm --no-deps mcp python -m pytest -q "$@"
    ;;

  probe)
    shift
    python3 "$REPO_ROOT/scripts/mcp_probe.py" --url "http://127.0.0.1:$MCP_PORT/mcp" "$@"
    ;;

  down)
    echo "[testing] stopping testing stack (data preserved in ./dbdata)"
    dc down
    ;;

  *)
    echo "unknown command: $cmd" >&2
    sed -n '3,20p' "$0"
    exit 2
    ;;
esac
