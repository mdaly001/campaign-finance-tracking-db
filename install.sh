#!/usr/bin/env bash
#
# cfdb one-command installer — California Campaign Finance Database
# Installs the full stack: PostgreSQL + ETL loader + MCP tool server (Docker Compose).
#
#   curl -fsSL https://raw.githubusercontent.com/mdaly001/campaign-finance-tracking-db/HEAD/install.sh | bash
#
# Or from a local checkout of this repo:  bash install.sh [flags]
#
# Flags (all optional; the curl|bash form runs with safe defaults and no prompts):
#   --dir DIR       install dir / repo checkout (default: $HOME/campaign-finance-db)
#   --project NAME  compose project name — volumes become <NAME>_pgdata and
#                   <NAME>_statecache (default: cfdb)
#   --no-etl        bring up db + migrations + mcp but skip the initial full load
#   --yes|-y        accepted for symmetry; the piped form never prompts anyway

set -euo pipefail

REPO_URL="${CFDB_REPO_URL:-https://github.com/mdaly001/campaign-finance-tracking-db.git}"
PROJECT="${CFDB_PROJECT:-cfdb}"
IMAGE="ghcr.io/mdaly001/cfdb-app:latest"   # published image; retagged to cfdb-app:
                                            # latest locally, which is what the
                                            # compose file resolves the app
                                            # services to

INSTALL_DIR="${CFDB_INSTALL_DIR:-$HOME/campaign-finance-db}"
RUN_ETL=1

C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_BOLD='\033[1m'; C_OFF='\033[0m'
log()  { printf "${C_GREEN}[cfdb]${C_OFF} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[cfdb!]${C_OFF} %s\n" "$*" >&2; }
die()  { printf "${C_RED}[cfdb x]${C_OFF} %s\n" "$*" >&2; exit 1; }

# ------------------------------------------------------------ flags ----
while [ $# -gt 0 ]; do
  case "$1" in
    --dir)     INSTALL_DIR="${2:-$INSTALL_DIR}"; shift ;;
    --project) PROJECT="${2:-$PROJECT}"; shift ;;
    --no-etl)  RUN_ETL=0 ;;
    --yes|-y)  ;;  # no prompts in this installer; accepted for compatibility
    -h|--help)
      sed -n '1,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) warn "ignoring unknown flag: $1" ;;
  esac
  shift || true
done

# --------------------------------------------------------- preflight ----
command -v docker >/dev/null 2>&1 || die "docker not found — install Docker Engine (Linux) or Docker Desktop (macOS) first"
docker info >/dev/null 2>&1 || die "docker daemon not reachable — start Docker (on Linux: sudo systemctl start docker) and re-run"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 required — 'docker compose version' failed; install Compose v2 and re-run"
command -v git >/dev/null 2>&1 || die "git not found — install git and re-run"

# --------------------------------------------------------- checkout ----
if git -C "$INSTALL_DIR" rev-parse -q --git-dir >/dev/null 2>&1; then
  log "updating existing checkout at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "git pull failed — continuing with what's already on disk"
elif [ ! -e "$INSTALL_DIR" ] || [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  log "cloning $REPO_URL -> $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR" || die "git clone failed (network or permissions?) — fix and re-run; the installer is safe to re-run"
else
  die "$INSTALL_DIR exists and is not an empty git checkout — re-run with --dir /some/other/path or bash install.sh --dir DIR"
fi
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
[ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE not found — that checkout doesn't look like this repo"

compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

# ------------------------------------------------------------- .env ----
ENVF="$INSTALL_DIR/.env"
if [ ! -f "$ENVF" ]; then
  PW="$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  printf 'DB_PASSWORD=%s\n' "$PW" > "$ENVF"
  log "generated a random DB password -> $ENVF (keep it safe; it cannot be recovered later)"
else
  log "keeping the existing DB password in $ENVF"
fi

# --------------------------------------------------------- image ----
# Prefer the published GHCR image (public, no auth needed); fall back to a
# local build from the checkout if the pull fails (private package, offline, …).
if docker pull "$IMAGE" >/dev/null 2>&1; then
  docker tag "$IMAGE" cfdb-app:latest
  log "using published image $IMAGE (retagged to cfdb-app:latest)"
else
  warn "couldn't pull $IMAGE — building the app image from the checkout instead"
  compose build
fi

# ------------------------------------------------------------ db ----
log "starting Postgres (compose project: $PROJECT)"
compose up -d db

# Wait until Postgres is healthy before migrating/loading.
CID="$(compose ps -q db | head -n 1)"
for _ in $(seq 1 90); do
  if [ -n "$CID" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$CID" 2>/dev/null)" = "healthy" ]; then
    break
  fi
  sleep 1
done
[ -n "$CID" ] || die "db never came up — check: docker compose -p $PROJECT -f $COMPOSE_FILE ps"

# -------------------------------------------------------- migrate ----
# Idempotent: the runner tracks applied migrations and skips ones already in.
log "applying migrations (idempotent)"
compose run --rm --no-deps --entrypoint python etl -m core.migrations.migrate --direction=up

# ----------------------------------------------------------- etl ----
if [ "$RUN_ETL" = 1 ]; then
  log "full ETL load — downloads ~1.5 GB from the State of California and can take hours"
  log "safe to interrupt with Ctrl+C: the loader checkpoints per table — re-running it resumes where it stopped"
  compose run --rm --no-deps etl | tee "$INSTALL_DIR/etl.log"
else
  warn "--no-etl: skipped the initial full load. Run it when ready with:"
  warn "  docker compose -p $PROJECT -f $COMPOSE_FILE run --rm --no-deps etl"
fi

# ----------------------------------------------------------- mcp ----
log "starting the MCP server"
compose up -d mcp

# -------------------------------------------------------- summary ----
echo
printf "${C_GREEN}${C_BOLD}Done.${C_OFF}\n\n"
printf "  db    compose project '%s' in %s (db password in %s/.env)\n" "$PROJECT" "$COMPOSE_FILE" "$INSTALL_DIR"
printf "  mcp   ${C_BOLD}http://localhost:9527/mcp${C_OFF} (read-only role: cfdb_reader)\n"
echo
echo "Day 2:"
echo "  status:        docker compose -p $PROJECT -f $COMPOSE_FILE ps"
echo "  logs:          docker compose -p $PROJECT -f $COMPOSE_FILE logs -f db|mcp"
echo "  incremental:   docker compose -p $PROJECT -f $COMPOSE_FILE run --rm --no-deps etl incremental --database-url postgresql://cfdb:<DB_PASSWORD from $ENVF>@db:5432/cfdb"
echo "  upgrades:      re-run this installer (pulls the latest image; migrations and load are idempotent)"
