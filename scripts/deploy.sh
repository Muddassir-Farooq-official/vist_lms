#!/usr/bin/env bash
# Runs ON THE VPS (called by GitHub Actions after the new code is copied over, through scripts/deploy_all.sh).
# Order: safety checks -> backup real DB -> build -> migrate -> restart -> health check.
# If anything fails the script stops and the OLD app keeps running.
#
#   bash scripts/deploy.sh                 Vertex (the institute in /opt/vist-lms, port 8000) - builds the image
#   bash scripts/deploy.sh --site <name>   another institute (/opt/lms-sites/<name>): uses the image Vertex's deploy built,
#                                          its own .env, database, files, port and Redis (docs/multi-institute.md)
set -euo pipefail
cd "$(dirname "$0")/.."
CODE_DIR="$PWD"
SITES_DIR="${LMS_SITES_DIR:-/opt/lms-sites}"

SITE=""
if [ "${1:-}" = "--site" ]; then
  SITE="${2:-}"
  printf '%s' "$SITE" | grep -Eq '^[a-z][a-z0-9]{1,19}$' || { echo "Use: bash scripts/deploy.sh --site <name>" >&2; exit 2; }
fi

log() { printf '\n\033[1;34m==> %s%s\033[0m\n' "${SITE:+[$SITE] }" "$*"; }
die() { printf '\n\033[1;31mDEPLOY FAILED%s: %s\033[0m\n' "${SITE:+ [$SITE]}" "$*" >&2; exit 1; }

if [ -z "$SITE" ]; then
  COMPOSE="docker compose -f docker-compose.prod.yml"
  ENV_FILE=".env"
  WORK_DIR="$CODE_DIR"
  PORT=8000
else
  WORK_DIR="$SITES_DIR/$SITE"
  [ -f "$WORK_DIR/compose.env" ] || die "$WORK_DIR/compose.env is missing (add the institute with scripts/server/sites.sh add)"
  COMPOSE="docker compose -p lms-$SITE -f $CODE_DIR/docker-compose.prod.yml --env-file $WORK_DIR/compose.env"
  ENV_FILE="$WORK_DIR/.env"
  PORT="$(grep -E '^LMS_PORT=' "$WORK_DIR/compose.env" | cut -d= -f2)"
  [ -n "$PORT" ] || die "LMS_PORT is missing in $WORK_DIR/compose.env"
fi
envval() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

log "1/6 Safety checks"
[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing on the server"
[ "$(envval APP_ENV)" = "production" ] || die "APP_ENV in $ENV_FILE must be 'production'"
DB_URL="$(envval DATABASE_URL)"
[ -n "$DB_URL" ] || die "DATABASE_URL is empty in $ENV_FILE"
case "$DB_URL" in *_test*|*sandbox*) die "$ENV_FILE points at a TEST/sandbox database. Refusing to deploy." ;; esac
if [ -n "$SITE" ]; then
  # an institute must never be pointed at another institute's database
  VERTEX_DB="$(grep -E '^DATABASE_URL_SYNC=' "$CODE_DIR/.env" | tail -1 | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')"
  SITE_DB="$(envval DATABASE_URL_SYNC | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')"
  [ -n "$SITE_DB" ] && [ "$SITE_DB" != "$VERTEX_DB" ] || die "$ENV_FILE uses Vertex's database ($SITE_DB). Refusing to deploy."
fi
mkdir -p "$WORK_DIR/var/storage" "$WORK_DIR/backups"
chmod 700 "$WORK_DIR/backups" 2>/dev/null || true
chown -R 1000:1000 "$WORK_DIR/var" 2>/dev/null || true

if [ "${SKIP_BACKUP:-0}" != "1" ]; then
  log "2/6 Backing up the REAL database (kept in backups/, last 10)"
  PG_URL="$(envval DATABASE_URL_SYNC | sed 's#^postgresql+psycopg://#postgresql://#')"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  docker run --rm -e PGURL="$PG_URL" -v "$WORK_DIR/backups:/backups" postgres:18-alpine \
    sh -c 'pg_dump --format=custom --no-owner "$PGURL" -f /backups/pre-deploy-'"$STAMP"'.dump' \
    || die "Database backup failed. Fix it (or run with SKIP_BACKUP=1 once) - nothing was changed."
  ls -1t "$WORK_DIR"/backups/pre-deploy-*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
else
  log "2/6 Backup skipped (SKIP_BACKUP=1)"
fi

if [ -z "$SITE" ]; then
  log "3/6 Building the image"
  $COMPOSE build app
else
  log "3/6 Using the image built for Vertex (same code)"
  docker image inspect vist-lms:latest >/dev/null 2>&1 || die "The vist-lms image is missing: deploy Vertex first."
fi

log "4/6 Applying database migrations to the REAL database"
$COMPOSE --profile tools run --rm migrate || die "Migration failed. The old app is still running; the database was not left half-migrated (each migration is transactional)."

log "5/6 Seeding any new roles/permissions (safe to repeat)"
$COMPOSE run --rm --no-deps app python scripts/seed_permissions.py || die "Permission seeding failed."

log "6/6 Restarting the app and worker"
if [ -z "$SITE" ]; then
  $COMPOSE up -d --remove-orphans
else
  $COMPOSE up -d --no-build --remove-orphans
fi

log "Waiting for the app to become healthy"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health/ready" >/dev/null 2>&1; then
    echo "Healthy after ~$((i*2))s"
    if [ -z "$SITE" ]; then
      docker image prune -f >/dev/null 2>&1 || true
      # Leftover build layers grow by hundreds of MB per deploy (8 GB seen on the VPS). The running app does not use
      # them; the next build only takes a little longer.
      docker builder prune -af >/dev/null 2>&1 || true
    fi
    curl -s "http://127.0.0.1:$PORT/health/ready"; echo
    log "Deploy finished OK"
    exit 0
  fi
  sleep 2
done
$COMPOSE logs --tail=80 app || true
die "The app did not become healthy within 60s (see logs above)."
