1|#!/usr/bin/env bash
2|#
3|# cfdb one-command installer — California Campaign Finance Database
4|#
5|#   curl -fsSL https://raw.githubusercontent.com/mdaly001/campaign-finance-tracking-db/master/install.sh | bash
6|#
7|# Provisions everything on one machine:
8|#   1. Docker (if missing)          4. A local LLM (by RAM tier)
9|#   2. PostgreSQL + schema          5. A browser chat UI (Open WebUI)
10|#   3. The CAL-ACCESS data load     6. The MCP server on :9527
11|#
12|# Flags:
13|#   --lite        everything except the local LLM (chat UI wired later)
14|#   --db-only     database + ETL + MCP server only — no LLM, no chat UI
15|#                 (use when you already host models/agents on your network)
16|#   --no-chat     skip the Open WebUI chat container (own frontend, e.g. Hermes)
17|#   --llm-url URL skip local model download; point the chat UI at this
18|#                 OpenAI-compatible URL (e.g. http://192.168.1.20:8080/v1)
19|#   --model-file P  serve a pre-downloaded local GGUF at path P (skips download)
20|#   --no-etl      skip the (long) initial data download for now
21|#   --model NAME  force model: qwen3-14b | gpt-oss-20b | qwen3.6-35b-a3b | coder-next-80b | none
22|#   --model-url U force an explicit GGUF download URL (resumable)
23|#   --dir PATH    install location (default: ~/campaign-finance-db)
24|#   --yes         accept prompts non-interactively
25|#
26|# Idempotent: re-running repairs and continues. Safe to run after failures.
27|
28|set -euo pipefail
29|
30|SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
31|[ -f "$SCRIPT_DIR/scripts/owui_bootstrap.sh" ] || SCRIPT_DIR="$(pwd)"
32|
33|REPO_URL="https://github.com/mdaly001/campaign-finance-tracking-db.git"
34|INSTALL_DIR="${CFDB_HOME:-$HOME/campaign-finance-db}"
35|MODEL_DIR=""                     # set after INSTALL_DIR is final
36|LITE=0; RUN_ETL=1; ASSUME_YES=0
37|RUN_CHAT=1; DB_ONLY=0; LLM_URL=""
38|MODEL_OVERRIDE=""; MODEL_URL_OVERRIDE=""; MODEL_FILE_PATH=""
39|LLM_PORT=8080
40|CHAT_PORT=3000
41|MCP_PORT=9527
42|
43|C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_BOLD='\033[1m'; C_OFF='\033[0m'
44|log()  { printf "${C_GREEN}[cfdb]${C_OFF} %s\n" "$*"; }
45|warn() { printf "${C_YELLOW}[cfdb !]${C_OFF} %s\n" "$*"; }
46|die()  { printf "${C_RED}[cfdb x]${C_OFF} %s\n" "$*" >&2; exit 1; }
47|pause_or_go() { [ "$ASSUME_YES" = 1 ] && return 0; printf "Press Enter to continue (Ctrl-C to stop)..."; read -r _; }
48|
49|# ---------------------------------------------------------------- args ----
50|while [ $# -gt 0 ]; do
51|  case "$1" in
52|    --lite) LITE=1 ;;
53|    --db-only) DB_ONLY=1; LITE=1; RUN_CHAT=0 ;;
54|    --no-chat) RUN_CHAT=0 ;;
55|    --llm-url) LLM_URL="${2:-}"; LITE=1; shift ;;
56|    --model-file) MODEL_FILE_PATH="${2:-}"; shift ;;
57|    --no-etl) RUN_ETL=0 ;;
58|    --model) MODEL_OVERRIDE="${2:-}"; shift ;;
59|    --model-url) MODEL_URL_OVERRIDE="${2:-}"; shift ;;
60|    --dir) INSTALL_DIR="${2:-}"; shift ;;
61|    --yes|-y) ASSUME_YES=1 ;;
62|    *) warn "ignoring unknown flag: $1" ;;
63|  esac; shift || true
64|done
65|
66|# ----------------------------------------------------------------- os -----
67|OS="$(uname -s)"
68|ARCH="$(uname -m)"
69|case "$OS" in
70|  Darwin) OSFAM=mac ;;
71|  Linux)  OSFAM=linux ;;
72|  MINGW*|MSYS*|CYGWIN*|Windows*)
73|    die "Windows shell detected. Run Linux tools inside WSL2:
74|      1. Open PowerShell as Admin:  wsl --install -d Ubuntu
75|      2. Reopen Ubuntu, then re-run this installer there." ;;
76|  *) die "unsupported OS: $OS" ;;
77|esac
78|log "OS: $OS ($ARCH)"
79|
80|# hardware probe -----------------------------------------------------------
81|if [ "$OSFAM" = mac ]; then
82|  RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
83|  DISK_FREE_KB=$(df -kP "$HOME" | awk 'NR==2{print $4}')
84|else
85|  RAM_BYTES=$(awk '/MemTotal/{print $2*1024}' /proc/meminfo 2>/dev/null || echo 0)
86|  DISK_FREE_KB=$(df -kP "$HOME" | awk 'NR==2{print $4}')
87|fi
88|RAM_GB=$(( RAM_BYTES / 1073741824 ))
89|DISK_FREE_GB=$(( DISK_FREE_KB / 1048576 ))
90|log "RAM: ${RAM_GB} GB   free disk: ${DISK_FREE_GB} GB"
91|
92|GPU=""
93|if [ "$OSFAM" = mac ]; then
94|  GPU="metal"
95|elif command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qi 'nvidia'; then
96|  GPU="cuda"
97|fi
98|[ -n "$GPU" ] && log "GPU: $GPU (accelerated inference)"
99|
100|[ "$DISK_FREE_GB" -lt 100 ] && warn "Less than 100 GB free. Data (~20 GB) + model (9-45 GB) may not fit comfortably."
101|
102|# model tier ---------------------------------------------------------------
103|pick_model() {
104|  if [ -n "$MODEL_OVERRIDE" ]; then echo "$MODEL_OVERRIDE"; return; fi
105|  if   [ "$RAM_GB" -ge 48 ]; then echo "coder-next-80b"
106|  elif [ "$RAM_GB" -ge 28 ]; then echo "qwen3.6-35b-a3b"
107|  elif [ "$RAM_GB" -ge 14 ]; then echo "qwen3-14b"
108|  else echo "none"; fi
109|}
110|MODEL="$(pick_model)"
111|model_file() { case "$1" in
112|  qwen3-14b)        echo "Qwen3-14B-Q4_K_M.gguf" ;;
113|  gpt-oss-20b)      echo "gpt-oss-20b-Q4_K_M.gguf" ;;
114|  qwen3.6-35b-a3b)  echo "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" ;;
115|  coder-next-80b)   echo "Qwen3-Coder-Next-Q4_K_M.gguf" ;;
116|esac; }
117|model_url() { case "$1" in
118|  qwen3-14b)        echo "https://huggingface.co/unsloth/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf" ;;
119|  gpt-oss-20b)      echo "https://huggingface.co/unsloth/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-Q4_K_M.gguf" ;;
120|  qwen3.6-35b-a3b)  echo "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" ;;
121|  coder-next-80b)   echo "https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf" ;;
122|esac; }
123|model_ctx() { case "$1" in
124|  qwen3-14b)       echo 8192 ;;
125|  gpt-oss-20b)     echo 16384 ;;
126|  qwen3.6-35b-a3b) echo 32768 ;;
127|  coder-next-80b)  echo 32768 ;;
128|esac; }
129|model_extra_args() { case "$1" in
130|  gpt-oss-20b) echo "--jinja" ;;   # gpt-oss chat template requires --jinja in llama.cpp
131|  *) echo "" ;;
132|esac; }
133|
134|[ -n "$MODEL_FILE_PATH" ] && [ "$DB_ONLY" = 0 ] && LITE=0
135|[ "$MODEL" = none ] && [ -z "$MODEL_FILE_PATH" ] && [ "$LITE" = 0 ] && { warn "Only ${RAM_GB} GB RAM — skipping local LLM (chat UI will need an API key later)."; LITE=1; }
136|
137|# summary -------------------------------------------------------------------
138|echo
139|printf "${C_BOLD}This will install:${C_OFF}
140|  PostgreSQL 16%s
141|  MCP server            http://localhost:${MCP_PORT}/mcp
142|" "$( [ "$RUN_ETL" = 1 ] && echo " + CAL-ACCESS data (initial load can take hours)" )"
143|if [ "$LITE" = 0 ]; then
144|  printf "  Local LLM (%s)      http://localhost:%s/v1\n" "$(basename "${MODEL_FILE_PATH:-$MODEL}")" "$LLM_PORT"
145|elif [ -n "$LLM_URL" ]; then
146|  printf "  Remote LLM          %s\n" "$LLM_URL"
147|fi
148|[ "$RUN_CHAT" = 1 ] && printf "  Browser chat UI     http://localhost:%s\n" "$CHAT_PORT"
149|echo "Install dir: ${INSTALL_DIR}"
150|echo
151|pause_or_go
152|
153|# docker --------------------------------------------------------------------
154|install_docker() {
155|  log "Installing Docker toolchain..."
156|  if [ "$OSFAM" = mac ]; then
157|    command -v brew >/dev/null 2>&1 || die "Homebrew missing. Install from https://brew.sh and re-run."
158|    brew install colima docker docker-compose
159|    colima start --cpu 4 --memory 8 || die "colima failed to start"
160|  else
161|    if command -v apt-get >/dev/null 2>&1; then
162|      curl -fsSL https://get.docker.com | sh -s -- --yes
163|      sudo usermod -aG docker "$USER" || true
164|      warn "Docker installed. If 'docker ps' fails in a NEW shell, log out/in (or run: newgrp docker)."
165|    elif command -v dnf >/dev/null 2>&1; then
166|      curl -fsSL https://get.docker.com | sh -s -- --yes
167|      sudo systemctl enable --now docker || true
168|    else
169|      die "No apt/dnf found. Install Docker manually, then re-run this script."
170|    fi
171|  fi
172|}
173|
174|if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
175|  log "Docker: $(docker --version)"
176|  docker info >/dev/null 2>&1 || die "Docker is installed but this shell cannot reach the daemon (permission denied on docker.sock).
177|  Fix: log out and back in (group membership lands in new shells), or:
178|    sudo usermod -aG docker \$USER   # then open a NEW terminal
179|  and re-run this installer."
180|else
181|  install_docker
182|  docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start it (Docker Desktop / 'colima start' / 'sudo systemctl start docker') and re-run."
183|fi
184|
185|# repo ----------------------------------------------------------------------
186|if [ -f "docker-compose.yml" ] && [ -f "pyproject.toml" ] && grep -q "cfdb" pyproject.toml; then
187|  log "Using current directory as the repo checkout."
188|else
189|  if [ -d "$INSTALL_DIR/.git" ]; then
190|    log "Repo exists at ${INSTALL_DIR}; pulling latest..."
191|    git -C "$INSTALL_DIR" pull --ff-only || warn "pull failed; continuing with existing checkout"
192|  else
193|    log "Cloning repo to ${INSTALL_DIR} ..."
194|    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
195|  fi
196|  cd "$INSTALL_DIR"
197|fi
198|MODEL_DIR="$INSTALL_DIR/models"
199|mkdir -p "$MODEL_DIR"
200|
201|# .env ----------------------------------------------------------------------
202|if [ ! -f .env ]; then
203|  DBPW="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
204|  echo "DB_PASSWORD=***" > .env
205|  log "Generated .env with a random DB password."
206|else
207|  log ".env already present; leaving it alone."
208|fi
209|grep -q '^DB_PASSWORD=' .env || { echo "DB_PASSWORD=change-…e-in-env" >> .env; warn ".env had no DB_PASSWORD — appended placeholder; set a real one and 'docker compose down && up -d' if the db was created with a different password."; }
210|
211|# db ------------------------------------------------------------------------
212|log "Starting PostgreSQL..."
213|docker compose up -d db
214|log "Waiting for PostgreSQL..."
215|for _ in $(seq 1 60); do
216|  if docker compose exec -T db pg_isready -q -U cfdb -d cfdb 2>/dev/null; then break; fi
217|  sleep 1
218|done
219|docker compose exec -T db pg_isready -q -U cfdb -d cfdb || die "PostgreSQL did not become ready"
220|log "PostgreSQL is up."
221|
222|# etl -----------------------------------------------------------------------
223|if [ "$RUN_ETL" = 1 ]; then
224|  if docker compose exec -T db psql -U cfdb -d cfdb -tAc "SELECT 1 FROM rcpt_cd LIMIT 1" 2>/dev/null | grep -q 1; then
225|    log "Data already loaded (rcpt_cd has rows) — running incremental check instead."
226|    docker compose run --rm etl -- incremental --database-url "postgresql://cfdb:$(grep '^DB_PASSWORD=' .env | cut -d= -f2)@db:5432/cfdb" || warn "incremental update failed (non-fatal)"
227|  else
228|    warn "Initial data load downloads a ~1.5 GB archive and takes a while (hours on slow links). You can leave this terminal; progress logs to the ETL container."
229|    docker compose run --rm etl || die "ETL failed — check output above; re-run this installer to resume."
230|  fi
231|else
232|  log "Skipping ETL (--no-etl). Load later with:  docker compose run --rm etl"
233|fi
234|
235|# mcp -----------------------------------------------------------------------
236|log "Starting MCP server on :${MCP_PORT} ..."
237|docker compose up -d mcp
238|
239|# llm -----------------------------------------------------------------------
240|LLM_UP=0
241|if [ "$LITE" = 0 ] && [ -n "$MODEL_FILE_PATH" ]; then
242|  [ -f "$MODEL_FILE_PATH" ] || die "--model-file not found: $MODEL_FILE_PATH"
243|  MODEL_DIR="$(cd "$(dirname "$MODEL_FILE_PATH")" && pwd)"
244|  GF="$(basename "$MODEL_FILE_PATH")"
245|  CTX=32768; EXTRA=""
246|  case "$GF" in *gpt-oss*) EXTRA="--jinja" ;; esac
247|  log "Using local model: $MODEL_DIR/$GF (ctx=${CTX})"
248|elif [ "$LITE" = 0 ]; then
249|  GF="$(model_file "$MODEL")"; URL="${MODEL_URL_OVERRIDE:-$(model_url "$MODEL")}"; CTX="$(model_ctx "$MODEL")"; EXTRA="$(model_extra_args "$MODEL")"
250|  if [ -z "$URL" ]; then warn "Unknown model '$MODEL'; skipping LLM serve."; LITE=1; fi
251|fi
252|if [ "$LITE" = 0 ]; then
253|  if [ -f "$MODEL_DIR/$GF" ]; then
254|    log "Model already downloaded: $GF"
255|  else
256|    log "Downloading $GF (resumable; Ctrl-C and re-run to resume)..."
257|    curl -L -f -C - --retry 5 -o "$MODEL_DIR/$GF" "$URL" \
258|      || die "Model download failed. The URL may have moved — find the Q4_K_M GGUF for $MODEL on huggingface.co and re-run with:
259|    --model-url <direct-gguf-url>"
260|  fi
261|  log "Starting model server (:${LLM_PORT}, ctx=${CTX}, q8 KV cache)..."
262|  if [ "$OSFAM" = mac ]; then
263|    command -v brew >/dev/null 2>&1 || die "brew required to run llama.cpp natively on macOS (needed for Metal GPU)"
264|    command -v llama-server >/dev/null 2>&1 || brew install llama.cpp
265|    # shellcheck disable=SC2086
266|    nohup llama-server -m "$MODEL_DIR/$GF" --ctx-size "$CTX" $EXTRA \
267|      --cache-type-k q8_0 --cache-type-v q8_0 \
268|      --host 0.0.0.0 --port "$LLM_PORT" > "$INSTALL_DIR/llama-server.log" 2>&1 &
269|    warn "llama-server listens on all interfaces (needed for the chat container to reach it on macOS). On a shared network, consider a firewall rule for port ${LLM_PORT}."
270|    echo "llama-server pid: $!  (log: $INSTALL_DIR/llama-server.log)"
271|  else
272|    GPU_ARGS=""
273|    [ "$GPU" = cuda ] && GPU_ARGS="--gpus=all"
274|    docker rm -f cfdb-llm >/dev/null 2>&1 || true
275|    # shellcheck disable=SC2086
276|    docker run -d --name cfdb-llm $GPU_ARGS --restart unless-stopped \
277|      -p "$LLM_PORT:$LLM_PORT" -v "$MODEL_DIR:/models" \
278|      ghcr.io/ggml-org/llama.cpp:server \
279|      -m "/models/$GF" --ctx-size "$CTX" $EXTRA --cache-type-k q8_0 --cache-type-v q8_0 \
280|      --host 0.0.0.0 --port "$LLM_PORT" >/dev/null
281|  fi
282|  log "Waiting for model to load (first request can be slow)..."
283|  for _ in $(seq 1 180); do
284|    curl -sf "http://localhost:${LLM_PORT}/v1/models" >/dev/null 2>&1 && { LLM_UP=1; break; }
285|    sleep 2
286|  done
287|  [ "$LLM_UP" = 1 ] && log "Model server is up." || warn "Model server not answering yet — it may still be loading. Check: $INSTALL_DIR/llama-server.log (mac) or 'docker logs cfdb-llm' (linux)."
288|fi
289|
290|# chat ui (Open WebUI) ------------------------------------------------------
291|if [ "$RUN_CHAT" = 1 ]; then
292|  CHAT_MODEL_BASE="${LLM_URL:-http://host.docker.internal:${LLM_PORT}/v1}"
293|  log "Starting browser chat UI (:${CHAT_PORT}) -> model ${CHAT_MODEL_BASE}"
294|  docker rm -f cfdb-chat >/dev/null 2>&1 || true
295|  HOST_GATEWAY=""
296|  [ "$OSFAM" = linux ] && HOST_GATEWAY="--add-host=host.docker.internal:host-gateway"
297|  # shellcheck disable=SC2086
298|  docker run -d --name cfdb-chat --restart unless-stopped \
299|    -p "$CHAT_PORT:8080" \
300|    -v cfdb-openwebui:/app/backend/data \
301|    $HOST_GATEWAY \
303|    -e OPENAI_API_KEY="***" \
304|    -e WEBUI_AUTH=False \
305|    -e DEFAULT_SYSTEM_PROMPT="You are a California campaign-finance analyst. For ANY factual question about committees, candidates, donors, contributions, expenditures, vendors, or ballot measures, you MUST call the attached CAL-ACCESS tools (cfdb_*) and answer only from their results — never from memory. If you are unsure which tool fits, call cfdb_get_server_docs first. If a tool returns nothing, say so plainly instead of guessing.
306|
307|CRITICAL RULES:
308|- Always call get_server_docs first to load data conventions before any query.
309|- Names are stored LAST-FIRST (e.g. payee_naml='Daly', namf='Michael Gomez').
310|- On rcpt_cd, cmte_id identifies the DONOR's committee, not the recipient. Always scope queries through filer_filings_cd, not cmte_id.
311|- 24-hour expenditure reports (s496_cd) have NO payee names — only a description. Vendor data from expn_cd is a lower bound; use rapid_expense_vendors to recover payees.
312|- Never trust a large total without spot-checking the raw rows for that filing_id — a filing may contain many different donors, not just the one you're interested in.
313|- If you join through filer_filings_cd to resolve a committee name, verify the result by checking the raw donor names on the individual rows for that filing_id.
314|- If a tool returns nothing, say so plainly instead of guessing." \
315|    ghcr.io/open-webui/open-webui:main >/dev/null
316|
317|  # Wire the MCP tools into Open WebUI so browser chats use the database
318|  # out of the box (Open WebUI does not auto-discover MCP servers).
319|  log "Wiring campaign-finance tools into the chat UI..."
320|  BOOT_KEY="$(bash "$SCRIPT_DIR/scripts/owui_bootstrap.sh" "http://localhost:${CHAT_PORT}" "http://host.docker.internal:${MCP_PORT}" cfdb-chat 2>>"$INSTALL_DIR/owui_bootstrap.log" | tail -1 || true)"
321|  if [ -n "$BOOT_KEY" ]; then
322|    grep -q '^OWUI_API_KEY=' .env 2>/dev/null || echo "$BOOT_KEY" >> .env
323|    log "Chat UI wired: model default 'Campaign Finance AI' + 15 cfdb tools attached."
324|  else
325|    warn "Chat auto-wiring incomplete (see $INSTALL_DIR/owui_bootstrap.log)."
326|    warn "Manual path: chat UI -> Admin Settings -> External Connections -> Tool Servers -> add http://host.docker.internal:${MCP_PORT}/mcp (type: MCP), then select the tools in any chat."
327|  fi
328|else
329|  log "Skipping chat UI (use your own frontend against http://localhost:${LLM_PORT}/v1)."
330|fi
331|
332|# smoke tests ---------------------------------------------------------------
333|# Streamable HTTP transport: POST an initialize handshake to /mcp and accept a
334|# JSON-RPC result (or an event-stream event) as proof the server answers tools.
335|MCP_PROBE="$(curl -sN -m 4 -H 'Accept: application/json, text/event-stream' \
336|  -H 'Content-Type: application/json' -X POST "http://localhost:${MCP_PORT}/mcp" \
337|  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"installer","version":"0"},"capabilities":{}}}' 2>/dev/null | head -c 400 || true)"
338|case "$MCP_PROBE" in
339|  *'"result"'*|*event:*) log "MCP server: OK (streamable-http at http://localhost:${MCP_PORT}/mcp)" ;;
340|  *)                     warn "MCP server: no /mcp response on :${MCP_PORT}" ;;
341|esac
342|[ "$LITE" = 0 ] && [ "$LLM_UP" = 1 ] && \
343|  ( curl -sf "http://localhost:${LLM_PORT}/v1/models" >/dev/null && log "LLM server: OK" || warn "LLM server: not ready yet" )
344|if [ "$RUN_CHAT" = 1 ]; then
345|  for _ in $(seq 1 60); do
346|    curl -s -o /dev/null -m 2 "http://localhost:${CHAT_PORT}" && break; sleep 2
347|  done
348|fi
349|
350|# done ----------------------------------------------------------------------
351|LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
352|if [ -z "$LAN_IP" ]; then
353|  LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
354|fi
355|if [ -z "$LAN_IP" ]; then
356|  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
357|fi
358|[ -z "$LAN_IP" ] && LAN_IP="localhost"
359|echo
360|printf "${C_GREEN}${C_BOLD}Done.${C_OFF}\n\n"
361|if [ "$RUN_CHAT" = 1 ]; then
362|  printf "Open ${C_BOLD}http://localhost:${CHAT_PORT}${C_OFF} in your browser and ask, for example:\n\n"
363|  printf "  \"Who are the top donors to any campaign in the database?\"\n\n"
364|  printf "The chat already has the campaign-finance tools attached\n(model: 'Campaign Finance AI') - no configuration needed.\n"
365|  printf "It will only use tools; nothing you ask leaves this machine.\n\n"
366|else
367|  log "Endpoints (also reachable on your LAN at ${LAN_IP:-<this-host-ip>}):"
368|  printf "  MCP:  http://%s:${MCP_PORT}/mcp   (point Hermes/Open WebUI/etc. at this)\n" "${LAN_IP:-<this-host-ip>}"
369|  [ "$LITE" = 0 ] && printf "  LLM:  http://%s:${LLM_PORT}/v1\n" "${LAN_IP:-<this-host-ip>}"
370|  echo
371|fi
372|[ "$RUN_ETL" = 0 ] && printf "NOTE: data load was skipped. Start it anytime:  cd %s && docker compose run --rm etl\n" "$INSTALL_DIR"
373|printf "\nDay-2 commands (from %s):\n" "$INSTALL_DIR"
374|printf "  docker compose ps                # what's running\n"
375|printf "  docker compose down              # stop everything (data survives)\n"
376|printf "  docker compose up -d             # start again\n"