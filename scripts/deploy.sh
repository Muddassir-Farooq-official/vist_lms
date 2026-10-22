#!/usr/bin/env bash
# Runs ON THE VPS (called by GitHub Actions after the new code is copied over).
# Order: safety checks -> backup real DB -> build -> migrate -> restart -> health check.
# If anything fails the script stops and the OLD app keeps running.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"
ENV_FILE=".env"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mDEPLOY FAILED: %s\033[0m\n' "$*" >&2; exit 1; }
envval() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

log "1/6 Safety checks"
[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing on the server"
[ "$(envval APP_ENV)" = "production" ] || die "APP_ENV in the server .env must be 'production'"
DB_URL="$(envval DATABASE_URL)"
[ -n "$DB_URL" ] || die "DATABASE_URL is empty in the server .env"
case "$DB_URL" in *_test*|*sandbox*) die "The server .env points at a TEST/sandbox database ($DB_URL). Refusing to deploy." ;; esac
mkdir -p var/storage backups
chown -R 1000:1000 var 2>/dev/null || true

if [ "${SKIP_BACKUP:-0}" != "1" ]; then
  log "2/6 Backing up the REAL database (kept in backups/, last 10)"
  PG_URL="$(envval DATABASE_URL_SYNC | sed 's#^postgresql+psycopg://#postgresql://#')"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  docker run --rm -e PGURL="$PG_URL" -v "$PWD/backups:/backups" postgres:18-alpine \
    sh -c 'pg_dump --format=custom --no-owner "$PGURL" -f /backups/pre-deploy-'"$STAMP"'.dump' \
    || die "Database backup failed. Fix it (or run with SKIP_BACKUP=1 once) - nothing was changed."
  ls -1t backups/pre-deploy-*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
else
  log "2/6 Backup skipped (SKIP_BACKUP=1)"
fi

log "3/6 Building the image"
$COMPOSE build app

log "4/6 Applying database migrations to the REAL database"
$COMPOSE --profile tools run --rm migrate || die "Migration failed. The old app is still running; the database was not left half-migrated (each migration is transactional)."

log "5/6 Seeding any new roles/permissions (safe to repeat)"
$COMPOSE run --rm --no-deps app python scripts/seed_permissions.py || die "Permission seeding failed."

log "6/6 Restarting the app and worker"
$COMPOSE up -d --remove-orphans

log "Waiting for the app to become healthy"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    echo "Healthy after ~$((i*2))s"
    docker image prune -f >/dev/null 2>&1 || true
    curl -s http://127.0.0.1:8000/health/ready; echo
    log "Deploy finished OK"
    exit 0
  fi
  sleep 2
done
$COMPOSE logs --tail=80 app || true
die "The app did not become healthy within 60s (see logs above)."