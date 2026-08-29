#!/usr/bin/env bash
# owui_bootstrap.sh — wire Open WebUI to the cfdb MCP server, zero clicks for end users.
#
# Why this exists: Open WebUI never auto-discovers MCP servers, and the MCP
# client it does ship (MCPClient) speaks Streamable HTTP only — it POSTs the
# initialize handshake to the full connection url, so the url must include the
# mount path (/mcp). Once connected, tools auto-attach only when the browser's
# chat request carries tool_ids ["server:mcp:cfdb"], which Chat.svelte sources
# from the workspace model's info.meta.toolIds. This script wires all of it:
#   1) seed admin API key  ->  2) auth verify  ->  3) register tool server
#   -> 4) create/update workspace model with info.meta.toolIds (browser reads it).
#
# Usage: owui_bootstrap.sh <chat_base_url> <mcp_url_for_chat_container> <chat_container>
#   e.g. owui_bootstrap.sh http://localhost:3000 http://host.docker.internal:9527 cfdb-chat
# Prints "OWUI_API_KEY=***" on the last line (persist it in .env).
set -uo pipefail
CHAT="${1:-http://localhost:3000}"
MCPU="${2:-http://host.docker.internal:9527}"
CTR="${3:-cfdb-chat}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[owui] %s\n' "$*" >&2; }
api()  { local m="$1" p="$2" d="${3:-}"; local o=(-s -m 30 -X "$m" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" "$CHAT$p")
  if [ -n "$d" ]; then curl "${o[@]}" -d "$d"; else curl "${o[@]}"; fi; }

# 1) Seed/lookup an API key for the auto-created admin (WEBUI_AUTH=False mode).
#    NOTE: Open WebUI only treats bearer tokens starting 'sk-' as API keys.
docker cp "$SCRIPT_DIR/owui_seed_key.py" "$CTR:/tmp/owui_seed_key.py" >/dev/null 2>&1
KEY="$(docker exec "$CTR" python3 /tmp/owui_seed_key.py)"
[ -n "$KEY" ] || { log "FAILED to seed API key"; exit 1; }

# 2) Verify auth; api-key enablement needs one container restart after first seeding.
if ! curl -sf -m 10 -H "Authorization: Bearer $KEY" "$CHAT/api/models" >/dev/null 2>&1; then
  log "restarting $CTR to activate API-key auth..."
  docker restart "$CTR" >/dev/null 2>&1
  for _ in $(seq 1 60); do curl -sf -m 3 "$CHAT/health" >/dev/null 2>&1 && break; sleep 2; done
fi
curl -sf -m 10 -H "Authorization: Bearer $KEY" "$CHAT/api/models" >/dev/null 2>&1 \
  || { log "API key key auth still failing"; exit 1; }

# 3) Register the cfdb MCP tool server (idempotent overwrite of our single entry).
#    url MUST include the /mcp mount path (MCPClient POSTs to the full url;
#    the 'path' field is ignored by the Streamable-HTTP client).
MCP_HOST="${MCPU#http://}"; MCP_HOST="${MCP_HOST%%/*}"; MCP_PORT="${MCP_HOST#*:}"; MCP_HOST="${MCP_HOST%%:*}"
api POST /api/v1/configs/tool_servers "{
  \"TOOL_SERVER_CONNECTIONS\": [{
    \"url\": \"http://${MCP_HOST}:${MCP_PORT}/mcp\",
    \"type\": \"mcp\",
    \"auth_type\": \"none\",
    \"headers\": null,
    \"key\": null,
    \"info\": {\"id\": \"cfdb\", \"name\": \"CAL-ACCESS campaign finance\"},
    \"config\": {\"enabled\": true}
  }]
}" >/dev/null || { log "MCP registration failed"; exit 1; }
log "MCP server registered (id: cfdb, streamable-http at /mcp)"

# 4) Attach tools to the model the browser will load by default: meta.toolIds is
#    the exact key Chat.svelte reads to auto-attach tools to every new chat.
MODELS_JSON="$(api GET /api/models)"
MODEL_ID="$(printf '%s' "$MODELS_JSON" | python3 -c '
import json,sys
d=json.load(sys.stdin)
data=d.get("data",d) if isinstance(d,dict) else d
print(data[0]["id"] if data else "")' 2>/dev/null)"
[ -n "$MODEL_ID" ] || { log "no models visible to Open WebUI"; exit 1; }

MODEL_NAME="Campaign Finance AI"
DESC="Ask about California campaign finance: donors, expenditures, committees, vendors."
BODY="{
  \"id\": \"${MODEL_ID}\",
  \"name\": \"${MODEL_NAME}\",
  \"params\": {\"toolIds\": [\"server:mcp:cfdb\"]},
  \"meta\": {\"description\": \"${DESC}\", \"toolIds\": [\"server:mcp:cfdb\"]},
  \"is_default\": true
}"
CODE="$(curl -s -o /dev/null -w "%{http_code}" -m 20 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d "$BODY" "$CHAT/api/v1/models/create")"
if [ "$CODE" != "200" ]; then
  # some builds 500 on /model/update when validation fails; patch the DB row directly (idempotent)
  log "model create/update returned $CODE, patching meta.toolIds directly via sqlite..."
  docker exec -i "$CTR" python3 - "$MODEL_ID" << 'PYEOF'
import json, sqlite3, sys, time
mid = sys.argv[1]
con = sqlite3.connect("/app/backend/data/webui.db")
row = con.execute("select meta from model where id=?", (mid,)).fetchone()
if not row:
    print("NO_MODEL_ROW", mid, file=sys.stderr); sys.exit(1)
meta = json.loads(row[0]) if row[0] else {}
meta["toolIds"] = ["server:mcp:cfdb"]
con.execute("update model set meta=?, updated_at=? where id=?", (json.dumps(meta), int(time.time()), mid))
con.commit()
PYEOF
fi
log "model default wired: ${MODEL_ID} (Campaign Finance AI, tools=cfdb)"
echo "OWUI_API_KEY=***"
