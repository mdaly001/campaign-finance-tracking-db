#!/usr/bin/env bash
#
# cfdb one-command installer — California Campaign Finance Database
#
#   curl -fsSL https://raw.githubusercontent.com/mdaly001/campaign-finance-tracking-db/master/install.sh | bash
#
# Provisions everything on one machine:
#   1. Docker (if missing)          4. A local LLM (by RAM tier)
#   2. PostgreSQL + schema          5. A browser chat UI (Open WebUI)
#   3. The CAL-ACCESS data load     6. The MCP server on :9527
#
# Flags:
#   --lite        everything except the local LLM (chat UI wired later)
#   --db-only     database + ETL + MCP server only — no LLM, no chat UI
#                 (use when you already host models/agents on your network)
#   --no-chat     skip the Open WebUI chat container (own frontend, e.g. Hermes)
#   --llm-url URL skip local model download; point the chat UI at this
#                 OpenAI-compatible URL (e.g. http://192.168.1.20:8080/v1)
#   --no-etl      skip the (long) initial data download for now
#   --model NAME  force model: qwen3-14b | gpt-oss-20b | qwen3.6-35b-a3b | coder-next-80b | none
#   --model-url U force an explicit GGUF download URL (resumable)
#   --dir PATH    install location (default: ~/campaign-finance-db)
#   --yes         accept prompts non-interactively
#
# Idempotent: re-running repairs/continues. Safe to run after failures.

set -euo pipefail

REPO_URL="https://github.com/mdaly001/campaign-finance-tracking-db.git"
INSTALL_DIR="${CFDB_HOME:-$HOME/campaign-finance-db}"
MODEL_DIR=""                     # set after INSTALL_DIR is final
LITE=0; RUN_ETL=1; ASSUME_YES=0
RUN_CHAT=1; DB_ONLY=0; LLM_URL=""
MODEL_OVERRIDE=""; MODEL_URL_OVERRIDE=""
LLM_PORT=8080
CHAT_PORT=3000
MCP_PORT=9527

C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_BOLD='\033[1m'; C_OFF='\033[0m'
log()  { printf "${C_GREEN}[cfdb]${C_OFF} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[cfdb !]${C_OFF} %s\n" "$*"; }
die()  { printf "${C_RED}[cfdb x]${C_OFF} %s\n" "$*" >&2; exit 1; }
pause_or_go() { [ "$ASSUME_YES" = 1 ] && return 0; printf "Press Enter to continue (Ctrl-C to stop)..."; read -r _; }

# ---------------------------------------------------------------- args ----
while [ $# -gt 0 ]; do
  case "$1" in
    --lite) LITE=1 ;;
    --db-only) DB_ONLY=1; LITE=1; RUN_CHAT=0 ;;
    --no-chat) RUN_CHAT=0 ;;
    --llm-url) LLM_URL="${2:-}"; LITE=1; shift ;;
    --no-etl) RUN_ETL=0 ;;
    --model) MODEL_OVERRIDE="${2:-}"; shift ;;
    --model-url) MODEL_URL_OVERRIDE="${2:-}"; shift ;;
    --dir) INSTALL_DIR="${2:-}"; shift ;;
    --yes|-y) ASSUME_YES=1 ;;
    *) warn "ignoring unknown flag: $1" ;;
  esac; shift || true
done

# ----------------------------------------------------------------- os -----
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) OSFAM=mac ;;
  Linux)  OSFAM=linux ;;
  MINGW*|MSYS*|CYGWIN*|Windows*)
    die "Windows shell detected. Run Linux tools inside WSL2:
      1. Open PowerShell as Admin:  wsl --install -d Ubuntu
      2. Reopen Ubuntu, then re-run this installer there." ;;
  *) die "unsupported OS: $OS" ;;
esac
log "OS: $OS ($ARCH)"

# hardware probe -----------------------------------------------------------
if [ "$OSFAM" = mac ]; then
  RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
  DISK_FREE_KB=$(df -kP "$HOME" | awk 'NR==2{print $4}')
else
  RAM_BYTES=$(awk '/MemTotal/{print $2*1024}' /proc/meminfo 2>/dev/null || echo 0)
  DISK_FREE_KB=$(df -kP "$HOME" | awk 'NR==2{print $4}')
fi
RAM_GB=$(( RAM_BYTES / 1073741824 ))
DISK_FREE_GB=$(( DISK_FREE_KB / 1048576 ))
log "RAM: ${RAM_GB} GB   free disk: ${DISK_FREE_GB} GB"

GPU=""
if [ "$OSFAM" = mac ]; then
  GPU="metal"
elif command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qi 'nvidia'; then
  GPU="cuda"
fi
[ -n "$GPU" ] && log "GPU: $GPU (accelerated inference)"

[ "$DISK_FREE_GB" -lt 100 ] && warn "Less than 100 GB free. Data (~20 GB) + model (9-45 GB) may not fit comfortably."

# model tier ---------------------------------------------------------------
pick_model() {
  if [ -n "$MODEL_OVERRIDE" ]; then echo "$MODEL_OVERRIDE"; return; fi
  if   [ "$RAM_GB" -ge 48 ]; then echo "coder-next-80b"
  elif [ "$RAM_GB" -ge 28 ]; then echo "qwen3.6-35b-a3b"
  elif [ "$RAM_GB" -ge 14 ]; then echo "qwen3-14b"
  else echo "none"; fi
}
MODEL="$(pick_model)"
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
  qwen3-14b)       echo 8192 ;;
  gpt-oss-20b)     echo 16384 ;;
  qwen3.6-35b-a3b) echo 32768 ;;
  coder-next-80b)  echo 32768 ;;
esac; }
model_extra_args() { case "$1" in
  gpt-oss-20b) echo "--jinja" ;;   # gpt-oss chat template requires --jinja in llama.cpp
  *) echo "" ;;
esac; }

[ "$MODEL" = none ] && [ "$LITE" = 0 ] && { warn "Only ${RAM_GB} GB RAM — skipping local LLM (chat UI will need an API key later)."; LITE=1; }

# summary -------------------------------------------------------------------
echo
printf "${C_BOLD}This will install:${C_OFF}
  PostgreSQL 16%s
  MCP server            http://localhost:${MCP_PORT}/sse
" "$( [ "$RUN_ETL" = 1 ] && echo " + CAL-ACCESS data (initial load can take hours)" )"
if [ "$LITE" = 0 ]; then
  printf "  Local LLM (%s)      http://localhost:%s/v1\n" "$MODEL" "$LLM_PORT"
elif [ -n "$LLM_URL" ]; then
  printf "  Remote LLM          %s\n" "$LLM_URL"
fi
[ "$RUN_CHAT" = 1 ] && printf "  Browser chat UI     http://localhost:%s\n" "$CHAT_PORT"
echo "Install dir: ${INSTALL_DIR}"
echo
pause_or_go

# docker --------------------------------------------------------------------
install_docker() {
  log "Installing Docker toolchain..."
  if [ "$OSFAM" = mac ]; then
    command -v brew >/dev/null 2>&1 || die "Homebrew missing. Install from https://brew.sh and re-run."
    brew install colima docker docker-compose
    colima start --cpu 4 --memory 8 || die "colima failed to start"
  else
    if command -v apt-get >/dev/null 2>&1; then
      curl -fsSL https://get.docker.com | sh -s -- --yes
      sudo usermod -aG docker "$USER" || true
      warn "Docker installed. If 'docker ps' fails in a NEW shell, log out/in (or run: newgrp docker)."
    elif command -v dnf >/dev/null 2>&1; then
      curl -fsSL https://get.docker.com | sh -s -- --yes
      sudo systemctl enable --now docker || true
    else
      die "No apt/dnf found. Install Docker manually, then re-run this script."
    fi
  fi
}

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "Docker: $(docker --version)"
else
  install_docker
  docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start it (Docker Desktop / 'colima start' / 'sudo systemctl start docker') and re-run."
fi

# repo ----------------------------------------------------------------------
if [ -f "docker-compose.yml" ] && [ -f "pyproject.toml" ] && grep -q "cfdb" pyproject.toml; then
  log "Using current directory as the repo checkout."
else
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Repo exists at ${INSTALL_DIR}; pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only || warn "pull failed; continuing with existing checkout"
  else
    log "Cloning repo to ${INSTALL_DIR} ..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  cd "$INSTALL_DIR"
fi
MODEL_DIR="$INSTALL_DIR/models"
mkdir -p "$MODEL_DIR"

# .env ----------------------------------------------------------------------
if [ ! -f .env ]; then
  DBPW="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  echo "DB_PASSWORD=***" > .env
  log "Generated .env with a random DB password."
else
  log ".env already present; leaving it alone."
fi
grep -q '^DB_PASSWORD=' .env || { echo "DB_PASSWORD=change-…e-in-env" >> .env; warn ".env had no DB_PASSWORD — appended placeholder; set a real one and 'docker compose down && up -d' if the db was created with a different password."; }

# db ------------------------------------------------------------------------
log "Starting PostgreSQL..."
docker compose up -d db
log "Waiting for PostgreSQL..."
for _ in $(seq 1 60); do
  if docker compose exec -T db pg_isready -q -U cfdb -d cfdb 2>/dev/null; then break; fi
  sleep 1
done
docker compose exec -T db pg_isready -q -U cfdb -d cfdb || die "PostgreSQL did not become ready"
log "PostgreSQL is up."

# etl -----------------------------------------------------------------------
if [ "$RUN_ETL" = 1 ]; then
  if docker compose exec -T db psql -U cfdb -d cfdb -tAc "SELECT 1 FROM rcpt_cd LIMIT 1" 2>/dev/null | grep -q 1; then
    log "Data already loaded (rcpt_cd has rows) — running incremental check instead."
    docker compose run --rm etl -- incremental --database-url "postgresql://cfdb:$(grep '^DB_PASSWORD=' .env | cut -d= -f2)@db:5432/cfdb" || warn "incremental update failed (non-fatal)"
  else
    warn "Initial data load downloads a ~1.5 GB archive and takes a while (hours on slow links). You can leave this terminal; progress logs to the ETL container."
    docker compose run --rm etl || die "ETL failed — check output above; re-run this installer to resume."
  fi
else
  log "Skipping ETL (--no-etl). Load later with:  docker compose run --rm etl"
fi

# mcp -----------------------------------------------------------------------
log "Starting MCP server on :${MCP_PORT} ..."
docker compose up -d mcp

# llm -----------------------------------------------------------------------
LLM_UP=0
if [ "$LITE" = 0 ]; then
  GF="$(model_file "$MODEL")"; URL="${MODEL_URL_OVERRIDE:-$(model_url "$MODEL")}"; CTX="$(model_ctx "$MODEL")"; EXTRA="$(model_extra_args "$MODEL")"
  if [ -z "$URL" ]; then warn "Unknown model '$MODEL'; skipping LLM serve."; LITE=1; fi
fi
if [ "$LITE" = 0 ]; then
  if [ -f "$MODEL_DIR/$GF" ]; then
    log "Model already downloaded: $GF"
  else
    log "Downloading $GF (resumable; Ctrl-C and re-run to resume)..."
    curl -L -f -C - --retry 5 -o "$MODEL_DIR/$GF" "$URL" \
      || die "Model download failed. The URL may have moved — find the Q4_K_M GGUF for $MODEL on huggingface.co and re-run with:
    --model-url <direct-gguf-url>"
  fi
  log "Starting model server (:${LLM_PORT}, ctx=${CTX}, q8 KV cache)..."
  if [ "$OSFAM" = mac ]; then
    command -v brew >/dev/null 2>&1 || die "brew required to run llama.cpp natively on macOS (needed for Metal GPU)"
    command -v llama-server >/dev/null 2>&1 || brew install llama.cpp
    # shellcheck disable=SC2086
    nohup llama-server -m "$MODEL_DIR/$GF" --ctx-size "$CTX" $EXTRA \
      --cache-type-k q8 --cache-type-v q8 \
      --host 0.0.0.0 --port "$LLM_PORT" > "$INSTALL_DIR/llama-server.log" 2>&1 &
    warn "llama-server listens on all interfaces (needed for the chat container to reach it on macOS). On a shared network, consider a firewall rule for port ${LLM_PORT}."
    echo "llama-server pid: $!  (log: $INSTALL_DIR/llama-server.log)"
  else
    GPU_ARGS=""
    [ "$GPU" = cuda ] && GPU_ARGS="--gpus=all"
    docker rm -f cfdb-llm >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    docker run -d --name cfdb-llm $GPU_ARGS --restart unless-stopped \
      -p "$LLM_PORT:$LLM_PORT" -v "$MODEL_DIR:/models" \
      ghcr.io/ggml-org/llama.cpp:server \
      -m "/models/$GF" --ctx-size "$CTX" $EXTRA --cache-type-k q8 --cache-type-v q8 \
      --host 0.0.0.0 --port "$LLM_PORT" >/dev/null
  fi
  log "Waiting for model to load (first request can be slow)..."
  for _ in $(seq 1 180); do
    curl -sf "http://localhost:${LLM_PORT}/v1/models" >/dev/null 2>&1 && { LLM_UP=1; break; }
    sleep 2
  done
  [ "$LLM_UP" = 1 ] && log "Model server is up." || warn "Model server not answering yet — it may still be loading. Check: $INSTALL_DIR/llama-server.log (mac) or 'docker logs cfdb-llm' (linux)."
fi

# chat ui (Open WebUI) ------------------------------------------------------
if [ "$RUN_CHAT" = 1 ]; then
  CHAT_MODEL_BASE="${LLM_URL:-http://host.docker.internal:${LLM_PORT}/v1}"
  log "Starting browser chat UI (:${CHAT_PORT}) -> model ${CHAT_MODEL_BASE}"
  docker rm -f cfdb-chat >/dev/null 2>&1 || true
  HOST_GATEWAY=""
  [ "$OSFAM" = linux ] && HOST_GATEWAY="--add-host=host.docker.internal:host-gateway"
  # shellcheck disable=SC2086
  docker run -d --name cfdb-chat --restart unless-stopped \
    -p "$CHAT_PORT:8000" \
    -v cfdb-openwebui:/app/backend/data \
    $HOST_GATEWAY \
    -e OPENAI_API_BASE_URL="$CHAT_MODEL_BASE" \
    -e OPENAI_API_KEY="***" \
    -e WEBUI_AUTH=False \
    ghcr.io/open-webui/open-webui:main >/dev/null
else
  log "Skipping chat UI (use your own frontend against http://localhost:${LLM_PORT}/v1)."
fi

# smoke tests ---------------------------------------------------------------
log "Smoke tests..."
if curl -s -o /dev/null -m 5 "http://localhost:${MCP_PORT}/sse"; then
  log "MCP server: OK (http://localhost:${MCP_PORT}/sse)"
else
  warn "MCP server: no response on :${MCP_PORT}"
fi
[ "$LITE" = 0 ] && [ "$LLM_UP" = 1 ] && \
  ( curl -sf "http://localhost:${LLM_PORT}/v1/models" >/dev/null && log "LLM server: OK" || warn "LLM server: not ready yet" )
if [ "$RUN_CHAT" = 1 ]; then
  for _ in $(seq 1 60); do
    curl -s -o /dev/null -m 2 "http://localhost:${CHAT_PORT}" && break; sleep 2
  done
fi

# done ----------------------------------------------------------------------
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "$LAN_IP" ] && LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
echo
printf "${C_GREEN}${C_BOLD}Done.${C_OFF}\n\n"
if [ "$RUN_CHAT" = 1 ]; then
  printf "Open ${C_BOLD}http://localhost:${CHAT_PORT}${C_OFF} in your browser and ask, for example:\n\n"
  printf "  \"Who are the top donors to any campaign in the database?\"\n\n"
  printf "To teach the chat about the campaign-finance tools, open the\nchat UI's MCP settings and paste this server URL:\n\n"
  printf "  http://host.docker.internal:${MCP_PORT}/sse\n\n"
else
  log "Endpoints (also reachable on your LAN at ${LAN_IP:-<this-host-ip>}):"
  printf "  MCP:  http://%s:${MCP_PORT}/sse   (point Hermes/Open WebUI/etc. at this)\n" "${LAN_IP:-<this-host-ip>}"
  [ "$LITE" = 0 ] && printf "  LLM:  http://%s:${LLM_PORT}/v1\n" "${LAN_IP:-<this-host-ip>}"
  echo
fi
[ "$RUN_ETL" = 0 ] && printf "NOTE: data load was skipped. Start it anytime:  cd %s && docker compose run --rm etl\n" "$INSTALL_DIR"
printf "\nDay-2 commands (from %s):\n" "$INSTALL_DIR"
printf "  docker compose ps                # what's running\n"
printf "  docker compose down              # stop everything (data survives)\n"
printf "  docker compose up -d             # start again\n"
