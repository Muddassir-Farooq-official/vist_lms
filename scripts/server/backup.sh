#!/usr/bin/env bash
# Nightly off-server backup of VIST LMS: the database and the uploaded files, copied to Google Drive with rclone.
# Runs ON THE VPS from /opt/vist-lms (cron, see docs/server-safety.md). Never prints a password.
#
#   bash scripts/server/backup.sh            make a backup now and copy it to the remote
#   bash scripts/server/backup.sh verify     download the newest database copy from the remote and check it can be restored
#   bash scripts/server/backup.sh list       show what is on the remote
#
# Keeps: the last 3 copies on the VPS (backups/nightly), 14 daily + 8 weekly (Sunday) copies on the remote.
# Settings in .env (optional):
#   BACKUP_REMOTE=vistbackup:        the rclone remote (default "vistbackup:"; use an encrypted "crypt" remote, see the guide)
# The result is recorded in the database, so the admin dashboard shows "Off-server backup: N h ago" or "failed".
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

ENV_FILE=".env"
PG_IMAGE="postgres:18-alpine"            # same as scripts/deploy.sh; pg_dump 18 reads older servers too
RCLONE_IMAGE="rclone/rclone:1.68"
RCLONE_CONF_DIR="/root/.config/rclone"
LOCAL_DIR="backups/nightly"
LOG="backups/backup.log"
KEEP_LOCAL=3
KEEP_DAILY_DAYS=15
KEEP_WEEKLY_DAYS=57

mkdir -p "$LOCAL_DIR"
chmod 700 backups 2>/dev/null || true
exec > >(tee -a "$LOG") 2>&1

envval() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' -e 's/[[:space:]]*#.*$//'; }
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

[ -f "$ENV_FILE" ] || { say "FAILED: $ENV_FILE not found (run from /opt/vist-lms)"; exit 1; }
PG_URL="$(envval DATABASE_URL_SYNC | sed 's#^postgresql+psycopg://#postgresql://#')"
[ -n "$PG_URL" ] || { say "FAILED: DATABASE_URL_SYNC is empty in .env"; exit 1; }
REMOTE="$(envval BACKUP_REMOTE)"; REMOTE="${REMOTE:-vistbackup:}"
case "$REMOTE" in *:*) ;; *) REMOTE="$REMOTE:";; esac
REMOTE="${REMOTE%/}"; case "$REMOTE" in *:) ;; *) REMOTE="$REMOTE/";; esac     # "name:" or "name:folder/"

psql_run() { docker run --rm -i -e PGURL="$PG_URL" "$PG_IMAGE" sh -c 'psql "$PGURL" -v ON_ERROR_STOP=1 -qtA' ; }
rclone() { docker run --rm -v "$RCLONE_CONF_DIR:/config/rclone" -v "$PWD/backups:/backups" "$RCLONE_IMAGE" "$@"; }

record() {   # record() ok|failed "detail" -> service_heartbeats, shown on the admin dashboard. Never fails the script.
  local name="backup"; [ "$1" = "failed" ] && name="backup-failed"
  local detail="${2//\'/}"
  printf "INSERT INTO service_heartbeats (name, last_seen_at, detail) VALUES ('%s', now(), '%s') ON CONFLICT (name) DO UPDATE SET last_seen_at = now(), detail = EXCLUDED.detail;\n" \
    "$name" "${detail:0:480}" | psql_run >/dev/null 2>&1 || say "(could not record the result on the dashboard)"
}
fail() { say "FAILED: $*"; record failed "$*"; exit 1; }

check_remote() {
  [ -s "$RCLONE_CONF_DIR/rclone.conf" ] || fail "rclone is not set up yet ($RCLONE_CONF_DIR/rclone.conf missing): see docs/server-safety.md, step 2"
  rclone lsd "$REMOTE" >/dev/null 2>&1 || fail "cannot reach the backup remote '$REMOTE' (Google Drive sign-in expired or the remote name is wrong)"
}

run_backup() {
  local stamp db files
  stamp="$(date +%Y%m%d-%H%M)"
  db="vist-db-$stamp.dump"; files="vist-files-$stamp.tar.gz"
  say "Backup started (remote: $REMOTE)"
  check_remote

  docker run --rm -e PGURL="$PG_URL" -v "$PWD/$LOCAL_DIR:/out" "$PG_IMAGE" \
    sh -c 'pg_dump --format=custom --no-owner "$PGURL" -f /out/'"$db" || fail "database dump failed"
  docker run --rm -v "$PWD/$LOCAL_DIR:/out" "$PG_IMAGE" sh -c 'pg_restore --list /out/'"$db"' >/dev/null' \
    || fail "the database dump is damaged (pg_restore cannot read it)"
  if [ -d var/storage ]; then
    tar -czf "$LOCAL_DIR/$files" -C var storage || fail "packing the uploaded files failed"
  else
    files=""
  fi
  chmod 600 "$LOCAL_DIR"/* 2>/dev/null || true

  rclone copy "/backups/nightly/$db" "${REMOTE}daily/" || fail "upload of the database copy failed"
  [ -n "$files" ] && { rclone copy "/backups/nightly/$files" "${REMOTE}daily/" || fail "upload of the files copy failed"; }
  if [ "$(date +%u)" = "7" ]; then                                   # Sunday: also keep it as a weekly copy
    rclone copy "/backups/nightly/$db" "${REMOTE}weekly/" || fail "weekly upload failed"
    [ -n "$files" ] && { rclone copy "/backups/nightly/$files" "${REMOTE}weekly/" || fail "weekly upload failed"; }
  fi
  rclone delete --min-age "${KEEP_DAILY_DAYS}d" "${REMOTE}daily/" >/dev/null 2>&1 || say "(could not tidy old daily copies)"
  rclone delete --min-age "${KEEP_WEEKLY_DAYS}d" "${REMOTE}weekly/" >/dev/null 2>&1 || say "(could not tidy old weekly copies)"
  ls -1t "$LOCAL_DIR"/vist-db-*.dump 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f
  ls -1t "$LOCAL_DIR"/vist-files-*.tar.gz 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f

  local size; size="$(du -h "$LOCAL_DIR/$db" | cut -f1)"
  say "Backup OK: database $size${files:+ + files} copied to $REMOTE"
  record ok "database $size${files:+ + files} to $REMOTE at $(date '+%d %b %H:%M')"
}

verify() {
  check_remote
  local newest
  newest="$(rclone lsf --files-only --include 'vist-db-*.dump' "${REMOTE}daily/" | sort | tail -1)"
  [ -n "$newest" ] || fail "no database copy found on $REMOTE"
  mkdir -p backups/verify
  rclone copy "${REMOTE}daily/$newest" /backups/verify/ || fail "download of $newest failed"
  local tables
  tables="$(docker run --rm -v "$PWD/backups/verify:/in" "$PG_IMAGE" sh -c 'pg_restore --list /in/'"$newest"' | grep -c " TABLE DATA "')" \
    || fail "$newest cannot be read by pg_restore"
  rm -f "backups/verify/$newest"
  say "Verify OK: $newest downloaded from $REMOTE and readable ($tables tables with data)."
}

case "${1:-run}" in
  run)    exec 9>backups/.backup.lock; flock -n 9 || { say "Another backup is still running; skipped."; exit 0; }; run_backup ;;
  verify) verify ;;
  list)   check_remote; rclone lsl "$REMOTE" ;;
  *)      echo "Use: bash scripts/server/backup.sh [run|verify|list]"; exit 2 ;;
esac
