#!/usr/bin/env bash
#
# cfdb one-command installer — California Campaign Finance Database
# Image-based install: pulls published images, no build on your machine.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/<branch>/install.sh | bash
#   bash install.sh [flags]
#
# Decision tree (every question can be pre-answered with a flag):
#   Q0  Anything beyond db + ETL + MCP tool server?      -> --db-only
#   Q1  Already running your own LLM server / endpoint?  -> --llm-url URL [--api-key K]
#       (answered "no" -> we serve a model on this machine) -> --model NAME | --model-url U | --model-file P
#   Q2  Need the Open WebUI chat interface?            -> --no-chat
#   Q3  Shared/team server or personal machine?        -> sets LAN vs localhost binding
#
# Flags:
#   --yes|-y       full install, all answers = defaults
#                  (local LLM served only if >= 14 GB RAM; chat UI on)
#   --db-only      database + ETL + MCP tool server only — no LLM, no chat UI
#   --no-llm       no LLM serving at all (chat UI waits for your endpoint/key later)
#   --llm-url U    don't serve a model — point the chat UI at this OpenAI-
#                  compatible endpoint instead (include the /v1 suffix)
#   --api-key K    API key your LLM endpoint expects (serve mode: one is
#                  generated if omitted and shared between the server and the chat UI)
#   --model N      serve tier N locally (qwen3-14b|gpt-oss-20b|qwen3.6-35b-a3b|
#                  coder-next-80b)
#   --model-url U  serve a specific GGUF download from U instead of a tier pick
#   --model-file P serve an already-downloaded GGUF at path P
#   --no-chat      skip the Open WebUI chat UI (own frontend, e.g. Hermes)
#   --no-etl       skip the initial full load (prints the run-when-ready command)
#   --tag TAG      pin the published cfdb-app image tag (default: latest)
#   --dir DIR      install dir (default: ~/campaign-finance-db)
#
# Running via `curl | bash` is safe: with no controlling TTY every prompt falls
# back to its default instead of consuming the rest of the piped script.
# Idempotent: re-running repairs/finishes an interrupted install.

set -euo pipefail

# ------------------------------------------------------------ flags ----
ASSUME_YES=0
DB_ONLY=0
RUN_CHAT=1
RUN_ETL=1
LLM_MODE=""              # "" -> ask (Q1); serve|remote|none once answered
LLM_URL=""
API_KEY=""
MODEL_OVERRIDE=""
MODEL_URL_OVERRIDE=""
MODEL_FILE_PATH=""
INSTALL_DIR="${CFDB_HOME:-$HOME/campaign-finance-db}"
CFDB_IMAGE="${CFDB_IMAGE:-ghcr.io/mdaly001/cfdb-app:latest}"
LLM_PORT=8080
CHAT_PORT=3000
MCP_PORT=9527
COMPOSE_FILE=""
LLM_KEY=""
LLM_BASE_URL=""

C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_BOLD='\033[1m'; C_OFF='\033[0m'
log()  { printf "${C_GREEN}[cfdb]${C_OFF} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[cfdb !]${C_OFF} %s\n" "$*" >&2; }
die()  { printf "${C_RED}[cfdb x]${C_OFF} %s\n" "$*" >&2; exit 1; }

# TTY-safe prompt: under `curl | bash` stdin is the script itself, so prompts
# must read from the controlling TTY and fall back to defaults without a TTY
# (a bare `read` would consume the rest of the piped script instead).
ask() { # ask VAR question [default] -> VAR=1 (yes) or 0 (no)
  local var="$1" question="$2" default="${3:-}" answer=""
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ] || ! : < /dev/tty; then
    answer="$default"
  else
    printf "%s [y/N]: " "$question" > /dev/tty
    IFS= read -r answer < /dev/tty || answer="$default"
    [ -z "$answer" ] && answer="$default"
  fi
  case "$answer" in [Yy]|[Yy]es|[Yy]?) answer=1 ;; *) answer=0 ;; esac
  printf -v "$var" '%s' "$answer"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)      ASSUME_YES=1 ;;
    --db-only)     DB_ONLY=1 ;;
    --no-llm)      LLM_MODE=none ;;
    --llm-url)     LLM_MODE=remote; LLM_URL="${2:-}"; shift ;;
    --api-key)     API_KEY="${2:-}"; shift ;;
    --model)       MODEL_OVERRIDE="${2:-}"; LLM_MODE="${LLM_MODE:-serve}"; shift ;;
    --model-url)   MODEL_URL_OVERRIDE="${2:-}"; LLM_MODE=serve; shift ;;
    --model-file)  MODEL_FILE_PATH="${2:-}"; LLM_MODE=serve; shift ;;
    --no-chat)     RUN_CHAT=0 ;;
    --no-etl)      RUN_ETL=0 ;;
    --tag)         CFDB_IMAGE="${CFDB_IMAGE%:*}:${2:-latest}"; shift ;;
    --dir)         INSTALL_DIR="${2:-}"; shift ;;
    *) warn "ignoring unknown flag: $1" ;;
  esac
  shift || true
done

# ------------------------------------------------------- probe ----
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) OSFAM=mac ;;
  Linux)  OSFAM=linux ;;
  MINGW*|MSYS*|CYGWIN*|Windows*)
    die "Windows shell detected. Run this installer inside WSL2 instead:
      1. Open PowerShell as Admin:  wsl --install -d Ubuntu
      2. Reopen Ubuntu, then re-run this installer there." ;;
  *) die "unsupported OS: $OS" ;;
esac
log "OS: $OS ($ARCH)"

if [ "$OSFAM" = mac ]; then
  RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
else
  RAM_BYTES=$(awk '/MemTotal/{print $2*1024}' /proc/meminfo 2>/dev/null || echo 0)
fi
RAM_GB=$(( RAM_BYTES / 1073741824 ))

GPU=""
if   command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then GPU=cuda
elif [ -e /dev/kfd ] || [ -e /sys/class/kfd ]; then GPU=amd
fi
[ -n "$GPU" ] && log "GPU: $GPU (accelerated inference)"

# model tiers (unsloth GGUFs; ctx sizes per model)
pick_model() {
  if [ -n "$MODEL_OVERRIDE" ]; then echo "$MODEL_OVERRIDE"; return; fi
  if   [ "$RAM_GB" -ge 48 ]; then echo "coder-next-80b"
  elif [ "$RAM_GB" -ge 28 ]; then echo "qwen3.6-35b-a3b"
  elif [ "$RAM_GB" -ge 14 ]; then echo "qwen3-14b"
  else echo "none"; fi
}
model_file() { case "$1" in
  qwen3-14b)        echo "Qwen3-14B-Q4_K_M.gguf" ;;
  gpt-oss-20b)      echo "gpt-oss-20b-Q4_K_M.gguf" ;;
  qwen3.6-35b-a3b)  echo "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" ;;
  coder-next-80b)   echo "Qwen3-Coder-Next-Q4_K_M.gguf" ;;
esac; }
model_url() { case "$1" in
  qwen3-14b)        echo "https://huggingface.co/unsloth/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf" ;;
  gpt-oss-20b)      echo "https://huggingface.co/unsloth/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-Q4_K_M.gguf" ;;
  qwen3.6-35b-a3b)  echo "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" ;;
  coder-next-80b)   echo "https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf" ;;
esac; }
model_ctx() { case "$1" in
  gpt-oss-20b)     echo 16384 ;;
  qwen3.6-35b-a3b) echo 32768 ;;
  coder-next-80b)  echo 32768 ;;
  *)               echo 8192 ;;
esac; }
model_extra_args() { case "$1" in
  gpt-oss-20b) echo "--jinja" ;;   # gpt-oss chat template requires --jinja in llama.cpp
  *) echo "" ;;
esac; }

# --------------------------------------------------- decision tree ----
if [ "$DB_ONLY" = 0 ]; then
  ask Q0 "Anything beyond the database + ETL + MCP tool server? (LLM serving + chat UI) [Y/n] " "y"
  [ "$Q0" = 0 ] && DB_ONLY=1
fi
# --db-only means "db + ETL + MCP only" — no LLM, no chat
[ "$DB_ONLY" = 1 ] && { LLM_MODE=none; RUN_CHAT=0; }

if [ "$DB_ONLY" = 0 ] && [ -z "$LLM_MODE" ]; then
  ask Q1 "Already running your own LLM server (Ollama/llama.cpp) or a cloud API endpoint? [y/N] " "n"
  if [ "$Q1" = 1 ]; then
    LLM_MODE=remote
    if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
      printf "Base URL of your endpoint (e.g. http://192.168.1.20:8080/v1): " > /dev/tty
      IFS= read -r LLM_URL < /dev/tty || LLM_URL=""
      printf "API key for it (empty = none): " > /dev/tty
      IFS= read -r API_KEY < /dev/tty || API_KEY=""
    fi
  else
    LLM_MODE=serve
  fi
fi
# full defaults (--yes) with no model fits -> no LLM at all
[ "$ASSUME_YES" = 1 ] && [ "$(pick_model)" = "none" ] && LLM_MODE=none

if [ "$DB_ONLY" = 0 ]; then
  ask Q2 "Need the Open WebUI chat interface (browser UI on :$CHAT_PORT)? [Y/n] " "y"
  [ "$Q2" = 1 ] && RUN_CHAT=1 || RUN_CHAT=0
  ask Q3 "Shared or team server (share via LAN), or personal machine (localhost only)? [S/p] " "p"
  [ "$Q3" = 1 ] && SHARED=1 || SHARED=0
else
  SHARED=0
fi
MCP_BIND=127.0.0.1
CHAT_BIND=127.0.0.1
[ "$SHARED" = 1 ] && { MCP_BIND=0.0.0.0; CHAT_BIND=0.0.0.0; warn "shared mode: MCP port and chat port bind to 0.0.0.0 (LAN) — the MCP server has no auth; keep it on a trusted network or put a reverse proxy in front."; }

# ------------------------------------------------------ docker ----
command -v docker >/dev/null 2>&1 || {
  log "Docker not found — installing"
  if [ "$OSFAM" = mac ]; then
    die "Docker is not installed on this Mac. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) or: brew install colima docker docker-compose && colima start — then re-run."
  fi
  command -v sudo >/dev/null 2>&1 || die "Docker is missing and sudo is unavailable; install Docker Engine for your distro first"
  curl -fsSL https://get.docker.com | sudo sh -s -- --yes || die "get.docker.com installer failed"
  usermod -aG docker "$(id -un)" 2>/dev/null || true
}
docker info >/dev/null 2>&1 || die "docker daemon not reachable — re-open your terminal (or 'newgrp docker') and re-run"
docker compose version >/dev/null 2>&1 || die "docker compose v2 required (docker compose version)"
# db survives reboots: db + mcp run restart=unless-stopped, but the daemon
# itself must start at boot — enable it on Linux where sudo is passwordless.
if [ "$OSFAM" = linux ] && command -v sudo >/dev/null 2>&1; then
  (systemctl is-enabled docker >/dev/null 2>&1 || sudo -n systemctl enable docker >/dev/null 2>&1) || true
fi

# ------------------------------------------- install dir / compose ----
REPO=""
command -v git >/dev/null 2>&1 && REPO="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
FIRST_RUN=0
mkdir -p "$INSTALL_DIR"
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd)"
if [ -f "$INSTALL_DIR/.env" ]; then
  FIRST_RUN=0
fi
# .env — generated once, preserved across re-runs (db password lives here)
if [ ! -f "$INSTALL_DIR/.env" ]; then
  FIRST_RUN=1
  PW="$(openssl rand -hex 12 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "DB_PASSWORD=***" > "$INSTALL_DIR/.env"
  log "generated DB password -> $INSTALL_DIR/.env (keep it safe)"
fi
# shellcheck disable=SC1091
. "$INSTALL_DIR/.env"

if [ -n "$REPO" ] && [ -f "$REPO/docker-compose.yml" ]; then
  # dev flow: running the installer from a repo checkout — use the repo compose
  # (builds locally) instead of the image-only compose generated below.
  COMPOSE_FILE="$REPO/docker-compose.yml"
  INSTALL_DIR="$REPO"
  log "repo checkout detected — installing from $COMPOSE_FILE (local build, not image pull)"
fi
if [ -z "$COMPOSE_FILE" ]; then
  # image-based compose (the normal user install): the only custom artifact is the
  # published cfdb-app image — mcp + etl run it directly, nothing is built here.
  cat > "$INSTALL_DIR/docker-compose.yml" <<EOF
# Generated by install.sh $(date +%F) — image-based install; no build context needed.
name: cfdb
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: cfdb
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: cfdb
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cfdb -d cfdb"]
      interval: 5s
      timeout: 3s
      retries: 10
  mcp:
    image: ${CFDB_IMAGE}
    restart: unless-stopped
    ports:
      - "${MCP_BIND}:${MCP_PORT}:9527"
    environment:
      DATABASE_URL: "postgresql://cfdb_reader:cfdb_reader@db:5432/cfdb"
      MCP_PORT: "9527"
    depends_on:
      db:
        condition: service_healthy
  etl:
    image: ${CFDB_IMAGE}
    environment:
      # migrate + etl run from the image; DATABASE_URL is interpolated from .env
      DATABASE_URL: "postgresql://cfdb:\${DB_PASSWORD}@db:5432/cfdb"
      SOS_RAW_DATA_URL: "https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip"
    volumes:
      - statecache:/app/state/cache
    entrypoint: ["python", "-m", "state.etl"]
    command: ["full", "--database-url", "postgresql://cfdb:\${DB_PASSWORD}@db:5432/cfdb"]
    depends_on:
      db:
        condition: service_healthy
volumes:
  pgdata:
  statecache:
EOF
  COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
fi
compose() { docker compose -p cfdb -f "$COMPOSE_FILE" "$@"; }

# -------------------------------------------------- 1) db + migrate ----
log "pulling images + starting db"
if [ -z "$REPO" ]; then
  # image-based install (no repo checkout present): pull-only, nothing is built here.
  compose pull db mcp etl || die "image pull failed — is $CFDB_IMAGE published? Public GHCR packages are pullable anonymously; private packages need 'docker login ghcr.io' first, then re-run."
fi
compose up -d db
for _ in $(seq 1 90); do
  CID="$(compose ps -q db | head -1)"
  if [ -n "$CID" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$CID")" = healthy ]; then break; fi
  sleep 1
done
[ -n "$CID" ] || die "db never came up — check: docker compose -p cfdb -f $COMPOSE_FILE logs db"

# migrations run inside the image (the image ships core/config/migrations);
# `run` runs them once against the freshly-created db; the migrator itself is
# idempotent, so re-runs are no-ops at the SQL level.
log "applying schema (migrate --direction=up, idempotent)"
# `--` is required: args after the service name starting with '-' are parsed as
# docker-compose run flags unless separated first.
compose run --rm --no-deps --entrypoint python etl -- -m core.migrations.migrate --direction=up

# ------------------------------------------------------ 2) ETL ----
# First run = full load (runs the etl service's default command, which embeds
# the DB URL from .env); re-runs run the incremental check instead.
# `--` before args: docker compose run would parse leading-dash args as its own flags.
ETL_RUN="docker compose -p cfdb -f $COMPOSE_FILE run --rm --no-deps etl --"
if [ "$RUN_ETL" = 1 ] && [ "$FIRST_RUN" = 1 ]; then
  log "initial ETL run (full) — this downloads ~1.5 GB and can take a while"
  compose run --rm --no-deps etl | tee "$INSTALL_DIR/etl.log"
elif [ "$RUN_ETL" = 1 ]; then
  log "re-run detected — running incremental check instead of the full load"
  compose run --rm --no-deps etl -- incremental --database-url "postgresql://cfdb:${DB_PASSWORD}@db:5432/cfdb" | tee "$INSTALL_DIR/etl-incremental.log"
else
  warn "--no-etl: skipping the initial load. Run when ready (--database-url is required; the service default runs 'full' with it built in):"
  warn "  docker compose -p cfdb -f \"$COMPOSE_FILE\" run --rm --no-deps etl -- full --database-url postgresql://cfdb:${DB_PASSWORD}@db:5432/cfdb"
  warn "  docker compose -p cfdb -f \"$COMPOSE_FILE\" run --rm --no-deps etl -- incremental --database-url postgresql://cfdb:${DB_PASSWORD}@db:5432/cfdb"
fi

# --------------------------------------------------- 3) MCP server ----
compose up -d mcp

# --------------------------------------------------- 4) LLM serve ----
MODEL_DIR="$INSTALL_DIR/models"
serve_model() {
  # serve <model-name> <model-file-path-on-host> <ctx> <extra-args> <api-key>
  local name="$1" file="$2" ctx="$3" extra="$4" key="$5"
  if [ "$OSFAM" = mac ]; then
    # macOS: serve natively (Docker on Mac has no GPU passthrough).
    command -v llama-server >/dev/null 2>&1 || { brew install llama.cpp || { warn "brew install llama.cpp failed — cannot serve a model locally"; return 1; }; }
    log "serving $name natively via llama-server on :$LLM_PORT"
    # shellcheck disable=SC2086
    nohup llama-server --model "$file" --port "$LLM_PORT" --api-key "$key" --ctx-size "$ctx" $extra > "$INSTALL_DIR/llm.log" 2>&1 &
  else
    docker rm -f cfdb-llm >/dev/null 2>&1 || true
    local img="ghcr.io/ggml-org/llama.cpp:server" g=""
    case "$GPU" in
      cuda) g="--gpus all" ;;
      amd)  img="ghcr.io/ggml-org/llama.cpp:server-rocm"
            docker image inspect "$img" >/dev/null 2>&1 || docker pull "$img" >/dev/null 2>&1 || img="ghcr.io/ggml-org/llama.cpp:server"
            g="--device=/dev/kfd --device=/dev/dri --group-add video --security-opt seccomp=unconfined" ;;
    esac
    # serve from the directory that actually holds the file (covers --model-file
    # paths that live outside $MODEL_DIR) — container serves it as /models/<base>
    # shellcheck disable=SC2086
    docker run -d --name cfdb-llm --restart unless-stopped --network cfdb_default $g \
      -p "${MCP_BIND}:$LLM_PORT:8080" \
      -v "$(dirname "$file"):/models" \
      "$img" --model "/models/$(basename "$file")" --port 8080 --api-key "$key" --ctx-size "$ctx" $extra
    log "serving $name in cfdb-llm (model=/models/$(basename "$file")) on :$LLM_PORT"
  fi
}

if [ "$DB_ONLY" = 0 ] && [ "$LLM_MODE" = "serve" ]; then
  MODEL="$(pick_model)"
  MODEL_SRC=""
  MODEL_FILE=""
  if [ -n "$MODEL_FILE_PATH" ]; then
    MODEL="custom"; MODEL_FILE="$MODEL_FILE_PATH"
  elif [ -n "$MODEL_URL_OVERRIDE" ]; then
    MODEL="custom"; MODEL_SRC="$MODEL_URL_OVERRIDE"; MODEL_FILE="$MODEL_DIR/$(basename "$MODEL_URL_OVERRIDE")"
  elif [ -n "$MODEL_OVERRIDE" ]; then
    MODEL="$MODEL_OVERRIDE"; MODEL_SRC="$(model_url "$MODEL")"; MODEL_FILE="$MODEL_DIR/$(model_file "$MODEL")"
  else
    MODEL_SRC="$(model_url "$MODEL")"
    MODEL_FILE="$MODEL_DIR/$(model_file "$MODEL")"
  fi
  if [ "$MODEL" = "none" ]; then
    warn "only ${RAM_GB} GB RAM — no local model fits; skipping LLM serving."
    warn "point the chat UI at an existing endpoint instead (--llm-url) and re-run."
  elif [ ! -f "$MODEL_FILE" ] && [ -z "$MODEL_SRC" ]; then
    warn "--model-file $MODEL_FILE does not exist and no download URL given — not serving."
  else
    mkdir -p "$MODEL_DIR"
    if [ ! -f "$MODEL_FILE" ]; then
      log "downloading $(basename "$MODEL_FILE") to $MODEL_DIR (resumable)"
      curl -fL --retry 5 --retry-delay 5 -C - --fail -o "$MODEL_FILE" "$MODEL_SRC" \
        || { warn "model download failed from $MODEL_SRC — not serving"; MODEL=""; }
    fi
    if [ -n "$MODEL" ] && [ -f "$MODEL_FILE" ]; then
      LLM_KEY="${API_KEY:-$(openssl rand -hex 16)}"
      echo "LLM_API_KEY=$LLM_KEY" >> "$INSTALL_DIR/.env"
      if serve_model "$MODEL" "$MODEL_FILE" "$(model_ctx "${MODEL_OVERRIDE:-$MODEL}")" "$(model_extra_args "${MODEL_OVERRIDE:-$MODEL}")" "$LLM_KEY"; then
        if [ "$OSFAM" = mac ]; then
          LLM_BASE_URL="http://host.docker.internal:$LLM_PORT/v1"
        else
          LLM_BASE_URL="http://cfdb-llm:8080/v1"
        fi
        log "LLM serving $MODEL on :$LLM_PORT (API key in .env)"
      fi
    fi
  fi
elif [ "$LLM_MODE" = "remote" ]; then
  [ -z "$LLM_URL" ] && die "--llm-url passed but the URL was empty"
  LLM_BASE_URL="$LLM_URL"
fi

# ----------------------------------------------------- 5) chat ----
if [ "$RUN_CHAT" = 1 ]; then
  log "starting chat UI (Open WebUI) on :$CHAT_PORT"
  docker rm -f cfdb-chat >/dev/null 2>&1 || true
  ADDHOST=""
  [ "$OSFAM" != "mac" ] && ADDHOST="--add-host=host.docker.internal:host-gateway"
  # chat runs on the compose default network -> resolves `mcp` service DNS and
  # reaches the host LLM serve via host-gateway.
  # shellcheck disable=SC2086
  docker run -d --name cfdb-chat --restart unless-stopped \
    --network cfdb_default $ADDHOST \
    -p "${CHAT_BIND}:${CHAT_PORT}:8080" \
    -e OPENAI_API_BASE_URL="${LLM_BASE_URL:-}" \
    -e OPENAI_API_KEY="${LLM_KEY:-${API_KEY:-none}}" \
    -e WEBUI_AUTH=False \
    -v cfdb-openwebui:/app/backend/data \
    ghcr.io/open-webui/open-webui:main \
    || warn "cfdb-chat failed to start — check 'docker logs cfdb-chat'"
  for _ in $(seq 1 60); do
    curl -s -m 3 -o /dev/null "http://localhost:${CHAT_PORT}/health" && break
    sleep 2
  done

  # Wire chat -> cfdb tools (mirrors scripts/owui_bootstrap.sh, self-contained —
  # no repo files needed on user machines):
  # 1) seed admin API key -> 2) verify auth (one restart after the first seed)
  # -> 3) register the cfdb tool server -> 4) create the default workspace model
  # with meta.toolIds so the cfdb tools auto-attach to every new chat.
  KEYF="$(mktemp)"
  (docker exec -i cfdb-chat python3 - > "$KEYF") <<'PY' || true
import json, secrets, sqlite3, sys, time
con = sqlite3.connect("/app/backend/data/webui.db")
row = con.execute("select id from user where role='admin' order by created_at limit 1").fetchone()
if not row:
    print("")  # no admin row yet -> caller warns and defers wiring to the next run
    sys.exit(0)
uid = row[0]
row = con.execute("select key from api_key where user_id=? and id like 'cfdb-bootstrap%'", (uid,)).fetchone()
if row:
    print(row[0])  # stable across re-runs: reuse the seeded key
    sys.exit(0)
key = "sk-cfdb-" + secrets.token_hex(16)
try:
    con.execute("insert into api_key (id, user_id, key, name, created_at, updated_at) values (?,?,?,?,?,?)",
                ("cfdb-bootstrap-" + secrets.token_hex(4), uid, key, "cfdb-installer", int(time.time()), int(time.time())))
except sqlite3.OperationalError:  # newer schema: key data lives in a JSON `data` column
    con.execute("insert into api_key (id, user_id, key, data, created_at, updated_at) values (?,?,?,?,?,?)",
                ("cfdb-bootstrap-" + secrets.token_hex(4), uid, key, json.dumps({"name": "cfdb-installer"}), int(time.time()), int(time.time())))
con.execute("insert or replace into config (key, value, updated_at) values ('auth.enable_api_keys', 'true', ?)", (int(time.time()),))
con.commit()
print(key)
PY
  KEY="$(cat "$KEYF")"
  rm -f "$KEYF"
  if [ -z "$KEY" ]; then
    warn "chat UI is up but webui.db had no admin user yet — re-run the installer after first boot to finish tool wiring"
  else
    echo "OWUI_API_KEY=$KEY" >> "$INSTALL_DIR/.env"
    log "seeded admin API key -> $INSTALL_DIR/.env (OWUI_API_KEY)"
    # API-key auth only activates after one restart following the first seed.
    if ! curl -sf -m 10 -H "Authorization: Bearer $KEY" "http://localhost:${CHAT_PORT}/api/models" >/dev/null 2>&1; then
      docker restart cfdb-chat > /dev/null 2>&1
      for _ in $(seq 1 30); do curl -s -m 3 -o /dev/null "http://localhost:${CHAT_PORT}/health" && break; sleep 2; done
    fi
    # register the cfdb MCP tool server — the URL MUST include the /mcp mount path
    # (the Streamable-HTTP client POSTs the initialize handshake to the full URL).
    curl -s -o /dev/null -m 30 -X POST \
      -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
      -d "{\"TOOL_SERVER_CONNECTIONS\":[{\"url\":\"http://mcp:9527/mcp\",\"type\":\"mcp\",\"auth_type\":\"none\",\"headers\":null,\"key\":null,\"info\":{\"id\":\"cfdb\",\"name\":\"CAL-ACCESS campaign finance\"},\"config\":{\"enabled\":true}}]}" \
      "http://localhost:${CHAT_PORT}/api/v1/configs/tool_servers" \
      || warn "tool-server registration failed — check 'docker logs cfdb-chat'"
    # create the "Campaign Finance AI" workspace model with tools attached
    # (params.toolIds/meta.toolIds = ["server:mcp:cfdb"]); fall back to patching
    # the DB row directly when /api/v1/models/create 500s on validation.
    (docker exec -i cfdb-chat python3 - "$KEY" "http://localhost:8080") <<'PY' || true
import json, sqlite3, sys, time, urllib.request
key, api = sys.argv[1], sys.argv[2]
def api_req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api + path, data=data, method=method,
                                headers={"Authorization": "Bearer " + key,
                                         "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"{}")
try:
    models = api_req("GET", "/api/models")
except Exception as e:
    print("models list failed: %s" % e, file=sys.stderr)
    sys.exit(1)
data = models.get("data", []) if isinstance(models, dict) else models
if not data:
    print("no models visible to Open WebUI yet (no LLM endpoint configured?)", file=sys.stderr)
    sys.exit(0)
base = data[0]["id"]
name = "Campaign Finance AI"
try:
    api_req("POST", "/api/v1/models/create", {
        "id": name, "name": name, "base_model_id": base,
        "params": {"model": base, "toolIds": ["server:mcp:cfdb"]},
        "meta": {"description": "Ask about California campaign finance: donors, expenditures, committees, vendors.",
                 "toolIds": ["server:mcp:cfdb"]},
        "is_default": True})
    print("created workspace model %s (base_model_id=%s)" % (name, base))
except Exception as e:
    print("model create failed: %s" % e, file=sys.stderr)
    con = sqlite3.connect("/app/backend/data/webui.db")
    row = con.execute("select id, name from model where id = ?", (base,)).fetchone()
    if row and row[1] == name:
        print("row already present: %s" % row[0], file=sys.stderr)
    else:
        print("no workspace model rows yet — tools attach on first chat instead", file=sys.stderr)
PY
    log "chat UI wired: tool server 'cfdb' (mcp:9527/mcp) + tools attached to default model"
  fi
fi

# ------------------------------------------------------ summary ----
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -z "$LAN_IP" ] && LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
[ -z "$LAN_IP" ] && LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
[ -z "$LAN_IP" ] && LAN_IP="localhost"

echo
printf "${C_GREEN}${C_BOLD}Done.${C_OFF}\n\n"
printf "  db    compose project 'cfdb' in %s (db password in %s/.env)\n" "$COMPOSE_FILE" "$INSTALL_DIR"
printf "  MCP   ${C_BOLD}http://%s:%s/mcp${C_OFF} (read-only role cfdb_reader)\n" "$LAN_IP" "$MCP_PORT"
if [ "$LLM_MODE" = "serve" ] && [ -n "$LLM_BASE_URL" ]; then
  printf "  LLM   serving ${C_BOLD}%s${C_OFF} on :%s (API key in .env; chat authenticates with it automatically)\n" "${MODEL:-custom}" "$LLM_PORT"
elif [ "$LLM_MODE" = "remote" ]; then
  printf "  LLM   endpoint: ${C_BOLD}%s${C_OFF} (chat UI authenticates with your key automatically)\n" "$LLM_URL"
fi
if [ "$RUN_CHAT" = 1 ]; then
  printf "  Chat  ${C_BOLD}http://localhost:%s${C_OFF} (admin key in .env)\n" "$CHAT_PORT"
  printf "  Tools attached to the default model: ${C_BOLD}server:mcp:cfdb${C_OFF} (no configuration needed)\n"
fi
echo
echo "Day 2:"
echo "  incremental re-check:  docker compose -p cfdb -f \"$COMPOSE_FILE\" run --rm --no-deps etl -- incremental --database-url postgresql://cfdb:***}@db:5432/cfdb"
echo "  status:                docker compose -p cfdb -f $COMPOSE_FILE ps"
echo "  restart after reboot:  docker start cfdb-chat && docker start cfdb-llm  # if you served LLM locally on Linux"
