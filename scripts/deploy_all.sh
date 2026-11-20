#!/usr/bin/env bash
# Runs ON THE VPS (GitHub Actions calls it after copying the new code). Deploys every institute on this server:
#   1. Vertex (/opt/vist-lms) first: builds the image, backs up, migrates, restarts, checks health.
#      If Vertex fails, nothing else is touched and the run fails.
#   2. Then every other institute in /opt/lms-sites/<name>/ (added with scripts/server/sites.sh add), one at a time,
#      each with its own backup -> migrate -> restart -> health check. One failing does not stop the others; the run
#      fails at the end so GitHub shows it, with a summary of which institute failed.
# An institute switched off with "sites.sh disable <name>" is skipped.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
SITES_DIR="${LMS_SITES_DIR:-/opt/lms-sites}"

printf '\n\033[1;35m######## Vertex ########\033[0m\n'
if ! bash scripts/deploy.sh; then
  printf '\n\033[1;31mVertex failed, so no other institute was touched (they keep running the previous version).\033[0m\n' >&2
  exit 1
fi

ok=(); failed=(); skipped=()
if [ -d "$SITES_DIR" ]; then
  for dir in "$SITES_DIR"/*/; do
    [ -f "$dir/site.env" ] || continue
    name="$(basename "$dir")"
    if grep -qx 'ENABLED=0' "$dir/site.env"; then skipped+=("$name"); continue; fi
    printf '\n\033[1;35m######## %s ########\033[0m\n' "$name"
    if bash scripts/deploy.sh --site "$name"; then ok+=("$name"); else failed+=("$name"); fi
  done
fi

# the server's scheduled jobs (nightly backups, monthly restore test, Backups-page requests): installed / refreshed
sched="skipped (deploy is not running as root: run 'sudo bash scripts/server/sites.sh schedule' once)"
if [ "$(id -u)" = "0" ]; then
  if bash scripts/server/sites.sh schedule >/dev/null 2>&1; then sched="up to date"; else sched="could not be updated (run: bash scripts/server/sites.sh schedule)"; fi
fi

printf '\n\033[1;34m==> Summary\033[0m\n'
printf '  Vertex: OK\n'
printf '  Scheduled jobs: %s\n' "$sched"
[ ${#ok[@]} -gt 0 ] && printf '  OK: %s\n' "${ok[*]}"
[ ${#skipped[@]} -gt 0 ] && printf '  Switched off (skipped): %s\n' "${skipped[*]}"
if [ ${#failed[@]} -gt 0 ]; then
  printf '\033[1;31m  FAILED: %s (each keeps running its previous version; see its log above)\033[0m\n' "${failed[*]}" >&2
  exit 1
fi
exit 0
