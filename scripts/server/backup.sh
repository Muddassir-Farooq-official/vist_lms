#!/usr/bin/env bash
# Nightly off-server backup of VIST LMS: the database and the uploaded files, copied to Google Drive with rclone.
# Runs ON THE VPS from /opt/vist-lms (cron, see docs/server-safety.md). Never prints a password.
#
#   bash scripts/server/backup.sh            make a backup now and copy it to the remote
#   bash scripts/server/backup.sh verify     download the newest database copy from the remote and check it can be restored
#   bash scripts/server/backup.sh list       show what is on the remote
#
# Keeps: the database - the last 3 copies on the VPS (backups/nightly), 14 daily + 8 weekly (Sunday) copies on the remote.
#        uploaded files - one up-to-date copy on the remote (files/current); each night only new or changed files are
#        uploaded, and a file that was changed or deleted is kept for 30 days in files/changed/<date>/.
# Settings in .env (optional):
#   BACKUP_REMOTE=vistbackup:        the rclone remote (default "vistbackup:"; use an encrypted "crypt" remote, see the guide)
# The result is recorded in the database, so the admin dashboard shows "Off-server backup: N h ago" or "failed".
#
# Other institutes on this server (docs/multi-institute.md): LMS_SITE_DIR=/opt/lms-sites/<name> bash scripts/server/backup.sh
# backs up THAT institute: its .env (own database), its files, its backups/ folder and its own BACKUP_REMOTE (an
# encrypted folder of its own). scripts/server/sites.sh backup-all runs this for every institute each night.
set -uo pipefail
cd "${LMS_SITE_DIR:-$(dirname "$0")/../..}" || exit 1

ENV_FILE=".env"
PG_IMAGE="postgres:18-alpine"            # same as scripts/deploy.sh; pg_dump 18 reads older servers too
RCLONE_IMAGE="rclone/rclone:1.68"
RCLONE_CONF_DIR="/root/.config/rclone"
LOCAL_DIR="backups/nightly"
LOG="backups/backup.log"
KEEP_LOCAL=3
KEEP_DAILY_DAYS=15
KEEP_WEEKLY_DAYS=57
KEEP_CHANGED_DAYS=31

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
rclone() { docker run --rm -v "$RCLONE_CONF_DIR:/config/rclone" -v "$PWD/backups:/backups" -v "$PWD/var/storage:/storage:ro" "$RCLONE_IMAGE" "$@"; }

record() {   # record() ok|failed "detail" -> service_heartbeats, shown on the admin dashboard. Never fails the script.
  local name="backup"; [ "$1" = "failed" ] && name="backup-failed"
  local detail="${2//\'/}"
  printf "INSERT INTO service_heartbeats (name, last_seen_at, detail) VALUES ('%s', now(), '%s') ON CONFLICT (name) DO UPDATE SET last_seen_at = now(), detail = EXCLUDED.detail;\n" \
    "$name" "${detail:0:480}" | psql_run >/dev/null 2>&1 || say "(could not record the result on the dashboard)"
}
# run_row ok|failed "detail" [db-file]: one row in backup_runs for the admin panel's Backups page (what ran, sizes, what is
# kept on the backup account). Best effort: never fails the script; skipped on a database without that table yet.
KIND="${BACKUP_KIND:-NIGHTLY}"; STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_row() {
  local ok="$1" detail="${2:-}" dbfile="${3:-}" listing="" dbb="" fb=""
  [ -n "$dbfile" ] && [ -f "$dbfile" ] && dbb="$(stat -c %s "$dbfile" 2>/dev/null)"
  [ -d var/storage ] && fb="$(du -sb --exclude=exports var/storage 2>/dev/null | cut -f1)"
  [ "$ok" = true ] && listing="$(rclone lsjson -R --files-only "$REMOTE" 2>/dev/null || true)"
  OK="$ok" DETAIL="$detail" DBB="$dbb" FB="$fb" LISTING="$listing" python3 - <<'PY' | psql_run >/dev/null 2>&1 || true
import json, os
def q(v):            # dollar-quoted literal; the text never contains the tag
    return "NULL" if v in (None, "") else "$v$" + str(v).replace("$v$", "") + "$v$"
copies, total, count = [], None, None
try:
    items = json.loads(os.environ["LISTING"]) if os.environ["LISTING"].strip() else None
except ValueError:
    items = None
if items is not None:
    total, count = sum(i.get("Size", 0) for i in items), len(items)
    for i in items:
        path = i.get("Path", "")
        folder = path.split("/", 1)[0]
        if folder in ("daily", "weekly") and path.endswith(".dump"):
            copies.append({"folder": folder, "name": path.split("/")[-1], "bytes": i.get("Size"), "at": i.get("ModTime")})
    copies.sort(key=lambda c: c["at"] or "", reverse=True)
print("INSERT INTO backup_runs (id, created_at, updated_at, kind, ok, started_at, finished_at, detail, db_bytes, files_bytes, "
      "remote_bytes, remote_objects, copies) VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, now(), %s, %s, %s, %s, %s, %s);" % (
      q(os.environ.get("KIND", "NIGHTLY")), os.environ["OK"], q(os.environ.get("STARTED")), q(os.environ["DETAIL"][:480]),
      os.environ["DBB"] or "NULL", os.environ["FB"] or "NULL", "NULL" if total is None else total, "NULL" if count is None else count,
      q(json.dumps(copies[:60])) + "::jsonb" if items is not None else "NULL"))
PY
}
export KIND STARTED
fail() { say "FAILED: $*"; record failed "$*"; run_row false "$*"; exit 1; }

check_remote() {
  [ -s "$RCLONE_CONF_DIR/rclone.conf" ] || fail "rclone is not set up yet ($RCLONE_CONF_DIR/rclone.conf missing): see docs/server-safety.md, step 2"
  # A new institute's folder does not exist on the remote yet: create it first (does nothing when it exists).
  rclone mkdir "$REMOTE" >/dev/null 2>&1 || true
  rclone lsd "$REMOTE" >/dev/null 2>&1 || fail "cannot reach the backup remote '$REMOTE' (the backup account's sign-in or key expired, or the remote name is wrong)"
}

run_backup() {
  local stamp db
  stamp="$(date +%Y%m%d-%H%M)"
  db="vist-db-$stamp.dump"
  say "Backup started (remote: $REMOTE)"
  check_remote

  docker run --rm -e PGURL="$PG_URL" -v "$PWD/$LOCAL_DIR:/out" "$PG_IMAGE" \
    sh -c 'pg_dump --format=custom --no-owner "$PGURL" -f /out/'"$db" || fail "database dump failed"
  docker run --rm -v "$PWD/$LOCAL_DIR:/out" "$PG_IMAGE" sh -c 'pg_restore --list /out/'"$db"' >/dev/null' \
    || fail "the database dump is damaged (pg_restore cannot read it)"
  chmod 600 "$LOCAL_DIR"/* 2>/dev/null || true

  rclone copy "/backups/nightly/$db" "${REMOTE}daily/" || fail "upload of the database copy failed"
  if [ "$(date +%u)" = "7" ]; then                                   # Sunday: also keep it as a weekly copy
    rclone copy "/backups/nightly/$db" "${REMOTE}weekly/" || fail "weekly upload failed"
  fi

  # Uploaded files: only what is new or changed since last night goes up. A file changed or deleted here is moved
  # into files/changed/<date>/ on the remote (not lost), and those are kept for $KEEP_CHANGED_DAYS days.
  local files_note=""
  if [ -d var/storage ]; then
    rclone sync /storage "${REMOTE}files/current" --backup-dir "${REMOTE}files/changed/$stamp" --exclude "/exports/**" --transfers 4 --checkers 8 \
      || fail "upload of the uploaded files failed"
    files_note=" + files ($(du -sh --exclude=exports var/storage 2>/dev/null | cut -f1))"
  fi

  rclone delete --min-age "${KEEP_DAILY_DAYS}d" "${REMOTE}daily/" >/dev/null 2>&1 || say "(could not tidy old daily copies)"
  rclone delete --min-age "${KEEP_WEEKLY_DAYS}d" "${REMOTE}weekly/" >/dev/null 2>&1 || say "(could not tidy old weekly copies)"
  rclone delete --min-age "${KEEP_CHANGED_DAYS}d" "${REMOTE}files/changed/" >/dev/null 2>&1 || true
  rclone rmdirs --leave-root "${REMOTE}files/changed/" >/dev/null 2>&1 || true
  ls -1t "$LOCAL_DIR"/vist-db-*.dump 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f
  rm -f "$LOCAL_DIR"/vist-files-*.tar.gz 2>/dev/null       # the old way (a full archive every night) is no longer used

  local size; size="$(du -h "$LOCAL_DIR/$db" | cut -f1)"
  say "Backup OK: database $size$files_note copied to $REMOTE"
  record ok "database $size$files_note to $REMOTE at $(date '+%d %b %H:%M')"
  run_row true "database $size$files_note" "$LOCAL_DIR/$db"
}

verify() {
  KIND=VERIFY; export KIND
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
  local files=""
  if [ -d var/storage ]; then
    rclone check /storage "${REMOTE}files/current" --one-way --size-only --exclude "/exports/**" >/dev/null 2>&1 \
      && files=" Uploaded files: the copy on the remote matches the server." \
      || files=" Uploaded files: the copy on the remote is behind the server (new files since the last backup are normal)."
  fi
  say "Verify OK: $newest downloaded from $REMOTE and readable ($tables tables with data).$files"
  KIND=VERIFY run_row true "$newest restored fine: $tables tables with data.$files"
}

case "${1:-run}" in
  run)    exec 9>backups/.backup.lock; flock -n 9 || { say "Another backup is still running; skipped."; exit 0; }; run_backup ;;
  verify) verify ;;
  list)   check_remote; rclone lsl "$REMOTE" ;;
  *)      echo "Use: bash scripts/server/backup.sh [run|verify|list]"; exit 2 ;;
esac
