#!/usr/bin/env bash
# Several institutes on one server (docs/multi-institute.md). Run ON THE VPS as root, from anywhere:
#
#   bash /opt/vist-lms/scripts/server/sites.sh list                       every institute: address, port, health, memory, last backup
#   bash /opt/vist-lms/scripts/server/sites.sh add <name> <domain> "<Full institute name>"
#                                                                         a new institute: own database + user, own secrets, own files,
#                                                                         own port and Redis, nginx + HTTPS, first admin, backups
#   bash /opt/vist-lms/scripts/server/sites.sh https <name>               (again) get the HTTPS certificate once the domain points here
#   bash /opt/vist-lms/scripts/server/sites.sh disable <name>             stop it (data kept; deploys skip it); "enable" starts it again
#   bash /opt/vist-lms/scripts/server/sites.sh backups-setup              one backup account (Backblaze B2 or Google Drive), an encrypted
#                                                                         folder per institute with its own passwords, nightly at 2:30 AM PKT
#   bash /opt/vist-lms/scripts/server/sites.sh backups-switch             move every institute's backups to Backblaze B2 (same passwords)
#   bash /opt/vist-lms/scripts/server/sites.sh backup <name|vertex>       back one institute up now;  verify <name|vertex>  test-restore it
#   bash /opt/vist-lms/scripts/server/sites.sh backup-all                 every institute, one after another (the nightly job)
#
# Vertex is the institute in /opt/vist-lms (port 8000) and is never changed by "add". The others live in
# /opt/lms-sites/<name>/: .env (secrets, mode 600), compose.env (port, files, workers), site.env (domain, name),
# var/storage (uploads) and backups/. Passwords are never printed, except the ones you must write down (shown once).
set -uo pipefail

CODE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SITES_DIR="${LMS_SITES_DIR:-/opt/lms-sites}"
PG_IMAGE="postgres:18-alpine"
RCLONE_IMAGE="rclone/rclone:1.68"
RCLONE_DIR="/root/.config/rclone"
STORE_CONF="$SITES_DIR/backup-store.conf"
FIRST_PORT=8001
NEED_MB=900                   # memory one more institute needs with 2 web workers (app + worker + sms + redis)
RESERVED="vertex vist admin www api mail root lms sites"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[1;31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }
need_root() { [ "$(id -u)" = "0" ] || die "Run as root (sudo)."; }
confirm() { printf '\n%s\nType YES to go ahead: ' "$1"; read -r a; [ "$a" = "YES" ] || die "Nothing was changed."; }
rnd() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-"$1"; }
envget() { [ -f "$2" ] && grep -E "^$1=" "$2" | tail -1 | cut -d= -f2- | sed -e 's/[[:space:]]\{1,\}#.*$//' -e 's/^["'\'']//' -e 's/["'\'']$//'; }
# setkey FILE KEY VALUE-VAR: set KEY=<value of the variable named VALUE-VAR> in FILE without printing the value
setkey() {
  F="$1" K="$2" V="${!3}" python3 - <<'PY'
import os, re
f, k, v = os.environ["F"], os.environ["K"], os.environ["V"]
lines = open(f).read().splitlines() if os.path.exists(f) else []
out, done = [], False
for line in lines:
    if re.match(rf"^{re.escape(k)}=", line):
        if not done:
            out.append(f"{k}={v}"); done = True
        continue
    out.append(line)
if not done:
    out.append(f"{k}={v}")
open(f, "w").write("\n".join(out) + "\n")
PY
}
rclone_run() { docker run --rm -i -v "$RCLONE_DIR:/config/rclone" "$RCLONE_IMAGE" "$@"; }
# Write one rclone remote: rc_section NAME TYPE, its settings as key=value lines on stdin, so keys, tokens and
# passwords never appear on a command line (ps, logs). Replaces a remote of the same name.
rc_section() {
  local f="$RCLONE_DIR/rclone.conf"; touch "$f"; chmod 600 "$f"
  NAME="$1" TYPE="$2" F="$f" python3 -c '
import configparser, os, sys
f = os.environ["F"]; c = configparser.RawConfigParser(); c.optionxform = str; c.read(f)
n = os.environ["NAME"]
if c.has_section(n):
    c.remove_section(n)
c.add_section(n); c.set(n, "type", os.environ["TYPE"])
for line in sys.stdin.read().splitlines():
    if "=" in line:
        k, v = line.split("=", 1); c.set(n, k, v)
with open(f, "w") as fh:
    c.write(fh)
'
}
obscure() { printf '%s' "$1" | rclone_run obscure -; }      # rclone's own scrambling for passwords kept in its config
site_compose() { echo "docker compose -p lms-$1 -f $CODE_DIR/docker-compose.prod.yml --env-file $SITES_DIR/$1/compose.env"; }
vertex_pgurl() { envget DATABASE_URL_SYNC "$CODE_DIR/.env" | sed 's#^postgresql+psycopg://#postgresql://#'; }
valid_name() { printf '%s' "$1" | grep -Eq '^[a-z][a-z0-9]{1,19}$'; }
site_dir() { [ "$1" = "vertex" ] && echo "$CODE_DIR" || echo "$SITES_DIR/$1"; }
all_names() { echo vertex; for d in "$SITES_DIR"/*/; do [ -f "$d/site.env" ] && basename "$d"; done; return 0; }

# SQL on stdin, run as a database administrator on Vertex's PostgreSQL server ($1 = database, default postgres).
# First with Vertex's own database user (if it may create roles and databases), else as the local "postgres" user.
pg_admin() {
  local db="${1:-postgres}" url
  url="$(URL="$(vertex_pgurl)" DB="$db" python3 -c 'import os;from urllib.parse import urlsplit,urlunsplit;u=urlsplit(os.environ["URL"]);print(urlunsplit(u._replace(path="/"+os.environ["DB"])))')"
  if [ "${PG_ADMIN_MODE:-}" = "local" ]; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -qtA -d "$db"
  else
    docker run --rm -i -e PGURL="$url" "$PG_IMAGE" sh -c 'psql "$PGURL" -v ON_ERROR_STOP=1 -qtA'
  fi
}
choose_pg_admin() {
  local can
  can="$(echo "SELECT (rolsuper OR (rolcreaterole AND rolcreatedb))::int FROM pg_roles WHERE rolname = current_user;" | PG_ADMIN_MODE="" pg_admin 2>/dev/null)"
  if [ "$can" = "1" ]; then PG_ADMIN_MODE=remote; ok "database administrator: Vertex's database user"; return; fi
  if id postgres >/dev/null 2>&1 && command -v psql >/dev/null && echo "SELECT 1;" | sudo -u postgres psql -qtA -d postgres >/dev/null 2>&1; then
    PG_ADMIN_MODE=local; ok "database administrator: the local postgres user"; return
  fi
  die "Neither Vertex's database user nor the local 'postgres' user may create databases. Send me the output of: sudo -u postgres psql -c '\\du'"
}

# ------------------------------------------------------------------ list
list() {
  printf '\n%-10s %-34s %-6s %-9s %-10s %s\n' "INSTITUTE" "ADDRESS" "PORT" "HEALTH" "MEMORY" "LAST BACKUP"
  local name dir port addr health mem last
  for name in $(all_names); do
    dir="$(site_dir "$name")"
    if [ "$name" = vertex ]; then port=8000; addr="$(envget PUBLIC_URL "$dir/.env")"; proj="vist-lms"
    else port="$(envget LMS_PORT "$dir/compose.env")"; addr="https://$(envget DOMAIN "$dir/site.env")"; proj="lms-$name"
      grep -qx 'ENABLED=0' "$dir/site.env" && addr="$addr (switched off)"; fi
    health="down"; curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && health="healthy"
    mem="$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | awk -v p="$proj-" '
      index($1, p) == 1 { v = $2; m = 1; if (v ~ /GiB$/) m = 1024; else if (v ~ /KiB$/) m = 1 / 1024; sub(/[KMG]iB$/, "", v); s += v * m }
      END { printf "%d MB", s }')"
    last="$(tail -n 50 "$dir/backups/backup.log" 2>/dev/null | grep -E 'Backup OK|FAILED' | tail -1 | cut -c1-16)"
    printf '%-10s %-34s %-6s %-9s %-10s %s\n' "$name" "${addr:-?}" "$port" "$health" "$mem" "${last:-never}"
  done
  printf '\nMemory free for more: %s MB (one more institute needs about %s MB)\n' "$(free -m | awk '/^Mem:/ {print $7}')" "$NEED_MB"
}

# ------------------------------------------------------------------ add
add() {
  need_root
  local name="${1:-}" domain="${2:-}" title="${3:-}"
  [ -n "$name" ] && [ -n "$domain" ] && [ -n "$title" ] || die 'Use: sites.sh add <name> <domain> "<Full institute name>"   e.g. sites.sh add abc lms.abccollege.edu.pk "ABC College of Nursing"'
  valid_name "$name" || die "The short name must be 2-20 lower-case letters/digits, starting with a letter (e.g. abc, kcon2)."
  case " $RESERVED " in *" $name "*) die "'$name' is reserved; choose another short name.";; esac
  printf '%s' "$title" | grep -Eq '^[A-Za-z0-9 .,&()/-]{3,120}$' || die "The institute name may use letters, digits, spaces and . , & ( ) / - only."
  printf '%s' "$domain" | grep -Eq '^([a-z0-9-]+\.)+[a-z]{2,}$' || die "That domain does not look right (e.g. lms.abccollege.edu.pk, lower-case, no https://)."
  local dir="$SITES_DIR/$name"
  [ -e "$dir" ] && die "$dir already exists. Choose another name (or see the guide to remove a half-made one)."
  for d in "$SITES_DIR"/*/; do [ -f "$d/site.env" ] && [ "$(envget DOMAIN "$d/site.env")" = "$domain" ] && die "$domain is already used by $(basename "$d")."; done
  [ -f "$CODE_DIR/.env" ] || die "Vertex's .env was not found in $CODE_DIR."
  docker image inspect vist-lms:latest >/dev/null 2>&1 || die "The vist-lms image is missing: deploy Vertex once first."
  command -v python3 >/dev/null || die "python3 is needed."

  say "Checks"
  local free_mb; free_mb="$(free -m | awk '/^Mem:/ {print $7}')"
  if [ "$free_mb" -lt $((NEED_MB + 300)) ]; then
    warn "only $free_mb MB of memory is free; one more institute needs about $NEED_MB MB."
    confirm "The server may become slow for EVERY institute. Continue anyway?"
  else ok "memory: $free_mb MB free"; fi
  local ip_dns ips; ip_dns="$(getent ahostsv4 "$domain" | awk '{print $1; exit}')"; ips="$(hostname -I 2>/dev/null) $(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || true)"
  local dns_ok=1
  if [ -z "$ip_dns" ]; then warn "$domain does not point anywhere yet (no DNS A record)."; dns_ok=0
  elif ! printf ' %s ' "$ips" | grep -q " $ip_dns "; then warn "$domain points to $ip_dns, not to this server ($ips)."; dns_ok=0
  else ok "$domain points to this server ($ip_dns)"; fi
  [ $dns_ok = 1 ] || warn "Everything else is set up now; HTTPS waits until the domain points here (then: sites.sh https $name)."
  choose_pg_admin
  local port=$FIRST_PORT
  while grep -qs "^LMS_PORT=$port$" "$SITES_DIR"/*/compose.env || ss -ltn 2>/dev/null | grep -q ":$port "; do port=$((port + 1)); done
  ok "port $port"

  local db="lms_$name" role="lms_$name"
  [ "$(echo "SELECT count(*) FROM pg_database WHERE datname = '$db';" | pg_admin)" = "0" ] || die "A database called $db already exists. Nothing was changed."
  [ "$(echo "SELECT count(*) FROM pg_roles WHERE rolname = '$role';" | pg_admin)" = "0" ] || die "A database user called $role already exists. Nothing was changed."
  confirm "Add institute '$title' as '$name':
  address    https://$domain   (nginx site lms-$name, port $port on this server only)
  database   $db with its own user $role and a new random password (Vertex's database is not touched)
  files      $dir
It does not stop or change Vertex or any other institute."

  # From here on, anything that fails is undone, so the next try starts clean.
  UNDO_DB=""; UNDO_DIR=""; UNDO_NGINX=""
  trap 'rc=$?; [ $rc -ne 0 ] && undo_add "$name"; exit $rc' EXIT

  say "1/8 Database and its own user"
  local dbpw; dbpw="$(rnd 32)"
  printf "CREATE ROLE %s LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE;\nCREATE DATABASE %s OWNER %s ENCODING 'UTF8' TEMPLATE template0;\nREVOKE ALL ON DATABASE %s FROM PUBLIC;\n" \
    "$role" "$dbpw" "$db" "$role" "$db" | pg_admin >/dev/null || die "Could not create the database."
  UNDO_DB="$db"
  echo "CREATE EXTENSION IF NOT EXISTS pg_trgm;" | pg_admin "$db" >/dev/null || die "Could not add the pg_trgm extension."
  ok "database $db, user $role"
  say "2/8 Folders and settings (secrets are new and random; nothing is copied from Vertex except harmless defaults)"
  mkdir -p "$dir/var/storage" "$dir/backups"; UNDO_DIR="$dir"
  chmod 750 "$dir"; chmod 700 "$dir/backups"; chown -R 1000:1000 "$dir/var"
  local vurl host jwt
  vurl="$(vertex_pgurl)"
  host="$(URL="$vurl" python3 -c 'import os;from urllib.parse import urlsplit;u=urlsplit(os.environ["URL"]);print(u.hostname+(":"+str(u.port) if u.port else ""))')"
  jwt="$(rnd 64)"
  : > "$dir/.env"; chmod 600 "$dir/.env"
  local K V
  for K in APP_ENV DEBUG INSTITUTE_TIMEZONE STORAGE_BACKEND STORAGE_LOCAL_PATH DB_POOL_SIZE DB_MAX_OVERFLOW TRUSTED_PROXIES SMS_NUMBER_FORMAT SMS_PROVIDER_METHOD; do
    V="$(envget "$K" "$CODE_DIR/.env")"; [ -n "$V" ] && setkey "$dir/.env" "$K" V
  done
  V=production; setkey "$dir/.env" APP_ENV V
  V=false; setkey "$dir/.env" DEBUG V; setkey "$dir/.env" ENABLE_STUDENT_PAYMENT_SIMULATION V
  V=true; setkey "$dir/.env" COOKIE_SECURE V
  V="\"$title\""; setkey "$dir/.env" APP_NAME V
  V="https://$domain"; setkey "$dir/.env" PUBLIC_URL V; setkey "$dir/.env" CORS_ORIGINS V
  V="postgresql+asyncpg://$role:$dbpw@$host/$db"; setkey "$dir/.env" DATABASE_URL V
  V="postgresql+psycopg://$role:$dbpw@$host/$db"; setkey "$dir/.env" DATABASE_URL_SYNC V
  setkey "$dir/.env" JWT_SECRET jwt
  V="lmsbk-$name:"; setkey "$dir/.env" BACKUP_REMOTE V
  V="$(envget VAPID_SUBJECT "$CODE_DIR/.env")"; [ -n "$V" ] && setkey "$dir/.env" VAPID_SUBJECT V
  unset dbpw jwt
  cat > "$dir/compose.env" <<EOF
# Read by docker compose for this institute (scripts/deploy.sh --site $name). No secrets here.
LMS_ENV_FILE=$dir/.env
LMS_STORAGE=$dir/var/storage
LMS_PORT=$port
LMS_WEB_WORKERS=2
EOF
  cat > "$dir/site.env" <<EOF
NAME=$name
DOMAIN=$domain
TITLE="$title"
ADDED=$(date +%F)
ENABLED=1
EOF
  ok "$dir"

  local C; C="$(site_compose "$name")"
  say "3/8 Checking the new database can be reached from the app containers"
  $C run --rm --no-deps app python -c "import asyncio,sqlalchemy as sa;from sqlalchemy.ext.asyncio import create_async_engine;from app.core.config import settings
async def m():
    e=create_async_engine(settings.database_url)
    async with e.connect() as c: await c.execute(sa.text('select 1'))
    await e.dispose()
asyncio.run(m())" >/dev/null 2>&1 \
    || die "The app cannot log in to $db as $role. PostgreSQL's pg_hba.conf probably lists Vertex's database/user only: add a line like
    host  $db  $role  172.16.0.0/12  scram-sha-256
(and the same for 192.168.0.0/16), reload PostgreSQL (systemctl reload postgresql), then run this command again."
  ok "connected"

  say "4/8 Push-notification keys"
  local out; out="$($C run --rm --no-deps app python scripts/push_keys.py 2>/dev/null)" || die "Could not make push keys."
  local vpub vpriv; vpub="$(printf '%s\n' "$out" | sed -n 's/^VAPID_PUBLIC_KEY=//p')"; vpriv="$(printf '%s\n' "$out" | sed -n 's/^VAPID_PRIVATE_KEY=//p')"
  [ -n "$vpub" ] && [ -n "$vpriv" ] || die "Could not read the push keys."
  setkey "$dir/.env" VAPID_PUBLIC_KEY vpub; setkey "$dir/.env" VAPID_PRIVATE_KEY vpriv; unset vpriv out
  ok "made"

  say "5/8 Tables (migrations), roles and the first administrator"
  $C --profile tools run --rm migrate >/dev/null || die "Migrations failed on the new database."
  LMS_ADMIN_PW="$(rnd 14)Aa1!"; export LMS_ADMIN_PW
  $C run --rm --no-deps -e LMS_ADMIN_PW app python scripts/seed_permissions.py --with-admin --admin-username admin --admin-password-env LMS_ADMIN_PW >/dev/null \
    || die "Could not create the roles and the first administrator."
  ok "tables, roles and user 'admin'"

  say "6/8 Starting it"
  $C up -d --no-build >/dev/null 2>&1 || die "Could not start the containers."
  local i; for i in $(seq 1 45); do curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && break; sleep 2; done
  curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 || { $C logs --tail=40 app; die "It did not become healthy."; }
  ok "healthy on port $port"

  say "7/8 Web address (nginx)"
  nginx_site "$name" "$domain" "$port" || die "nginx did not accept the new site."
  trap - EXIT          # the institute is complete and running; HTTPS and backups below can be done again separately
  if [ $dns_ok = 1 ]; then https "$name" || warn "HTTPS failed for now: fix the domain, then run: sites.sh https $name"
  else warn "HTTPS skipped: when $domain points to this server run: sites.sh https $name"; fi

  say "8/8 Backups"
  if [ -f "$STORE_CONF" ]; then site_backup_remote "$name" && backup_one "$name" && verify_one "$name" || warn "Backup set-up did not finish: run sites.sh backups-setup"
  else warn "No backup account yet: run   sites.sh backups-setup   (sets up backups for every institute at once)"; fi

  printf '\n\033[1;32m%s is ready.\033[0m\n' "$title"
  printf '\n\033[1;33m%s\033[0m\n' "FIRST LOGIN - write this down and give it only to that institute's administrator (shown once):"
  printf '    address:   https://%s\n    username:  admin\n    password:  %s\n' "$domain" "$LMS_ADMIN_PW"
  printf 'They must choose a new password at first login. Then: Settings -> institute name, logo, fee and SMS settings.\n'
  unset LMS_ADMIN_PW
  printf 'From now on every approved deploy updates it too (after Vertex).\n'
}

undo_add() {
  local name="$1"
  printf '\n\033[1;33mUndoing the half-made institute %s ...\033[0m\n' "$name" >&2
  [ -f "$SITES_DIR/$name/compose.env" ] && $(site_compose "$name") down -v >/dev/null 2>&1
  [ -n "${UNDO_NGINX:-}" ] && { rm -f "$UNDO_NGINX" "/etc/nginx/sites-enabled/lms-$name.conf"; nginx -t >/dev/null 2>&1 && systemctl reload nginx; }
  if [ -n "${UNDO_DB:-}" ]; then
    printf 'DROP DATABASE IF EXISTS %s;\nDROP ROLE IF EXISTS %s;\n' "$UNDO_DB" "$UNDO_DB" | pg_admin >/dev/null 2>&1 \
      || printf 'Could not remove database %s: remove it by hand (DROP DATABASE %s; DROP ROLE %s;)\n' "$UNDO_DB" "$UNDO_DB" "$UNDO_DB" >&2
  fi
  [ -n "${UNDO_DIR:-}" ] && rm -rf "$UNDO_DIR"
  printf 'Undone: nothing of %s is left; Vertex and the other institutes were not touched.\n' "$name" >&2
}

nginx_site() {
  local name="$1" domain="$2" port="$3" conf
  command -v nginx >/dev/null || { warn "nginx is not installed"; return 1; }
  if [ -d /etc/nginx/sites-available ]; then conf="/etc/nginx/sites-available/lms-$name.conf"; else conf="/etc/nginx/conf.d/lms-$name.conf"; fi
  cat > "$conf" <<EOF
# $domain -> institute "$name" (port $port). Made by scripts/server/sites.sh; certbot adds the HTTPS part.
server {
    listen 80;
    server_name $domain;
    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:$port;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$remote_addr;   # not \$proxy_add_x_forwarded_for (audit SEC-02)
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
  UNDO_NGINX="$conf"
  [ -d /etc/nginx/sites-enabled ] && ln -sf "$conf" "/etc/nginx/sites-enabled/lms-$name.conf"
  if nginx -t >/dev/null 2>&1; then systemctl reload nginx && ok "nginx: $domain -> port $port"; return 0; fi
  rm -f "$conf" "/etc/nginx/sites-enabled/lms-$name.conf"; UNDO_NGINX=""
  nginx -t; return 1
}

https() {
  need_root
  local name="${1:-}"; [ -f "$SITES_DIR/$name/site.env" ] || { warn "No institute called '$name'."; return 1; }
  local domain; domain="$(envget DOMAIN "$SITES_DIR/$name/site.env")"
  command -v certbot >/dev/null || { warn "certbot is not installed (apt install certbot python3-certbot-nginx)"; return 1; }
  local email; email="$(envget VAPID_SUBJECT "$CODE_DIR/.env" | sed 's/^mailto://')"
  [ -n "$email" ] || { printf 'E-mail for certificate expiry notices: '; read -r email; }
  certbot --nginx -d "$domain" --non-interactive --agree-tos -m "$email" --redirect >/dev/null 2>&1 \
    || { warn "certbot could not get a certificate for $domain (does it point to this server?)"; return 1; }
  ok "HTTPS for https://$domain (renews by itself)"
}

disable() {
  need_root; local name="${1:-}"; [ -f "$SITES_DIR/$name/site.env" ] || die "No institute called '$name'."
  confirm "Stop $name? Its data, files and backups are kept; deploys skip it until 'enable'."
  $(site_compose "$name") stop >/dev/null 2>&1
  V=0; setkey "$SITES_DIR/$name/site.env" ENABLED V; ok "$name is switched off"
}
enable() {
  need_root; local name="${1:-}"; [ -f "$SITES_DIR/$name/site.env" ] || die "No institute called '$name'."
  V=1; setkey "$SITES_DIR/$name/site.env" ENABLED V
  bash "$CODE_DIR/scripts/deploy.sh" --site "$name" && ok "$name is running the current version again"
}

# ------------------------------------------------------------------ backups
backups_setup() {
  need_root
  mkdir -p "$RCLONE_DIR" "$SITES_DIR"; chmod 700 "$RCLONE_DIR"
  if [ -f "$STORE_CONF" ] && rclone_run lsd "$(envget BASE "$STORE_CONF" | cut -d: -f1):" >/dev/null 2>&1; then
    ok "the backup account is already connected ($(envget PROVIDER "$STORE_CONF"))"
  else
    printf '\nWhere should the backups go?\n  1) Backblaze B2 (recommended: reliable, about $6 per TB a month)\n  2) Google Drive (free 15 GB; use a Google account only for backups)\nChoose 1 or 2: '
    local c; read -r c
    case "$c" in
      1) connect_b2 lmsstore
         printf 'PROVIDER=b2\nBASE=lmsstore:%s\n' "$B2_BUCKET" > "$STORE_CONF" ;;
      2) printf '\nOn your Windows PC: download rclone from https://rclone.org/downloads/ , unzip, open PowerShell in that folder and run:\n    .\\rclone.exe authorize "drive"\nSign in with the BACKUP Google account and copy the text between ---> and <---.\nPaste it here (not shown): '
         local token; read -rs token; echo
         case "$token" in *access_token*refresh_token*) ;; *) die "That does not look like the token. Nothing was changed.";; esac
         printf 'scope=drive\ntoken=%s\n' "$token" | rc_section lmsstore drive || die "Could not save the Google Drive connection."
         unset token
         rclone_run lsd lmsstore: >/dev/null 2>&1 || { rclone_run config delete lmsstore >/dev/null 2>&1; die "Google Drive did not accept the token. Nothing was kept."; }
         printf 'PROVIDER=drive\nBASE=lmsstore:LMS-backups\n' > "$STORE_CONF" ;;
      *) die "Nothing was changed." ;;
    esac
    chmod 600 "$STORE_CONF"; ok "backup account connected"
  fi
  local name
  for name in $(all_names); do
    say "Backups for $name"
    site_backup_remote "$name" && backup_one "$name" && verify_one "$name" || warn "$name: backup set-up did not finish (see $(site_dir "$name")/backups/backup.log)"
  done
  local line="30 21 * * * bash $CODE_DIR/scripts/server/sites.sh backup-all >/dev/null 2>&1"
  local tz; tz="$(timedatectl show -p Timezone --value 2>/dev/null || echo Etc/UTC)"
  [ "$tz" = "Asia/Karachi" ] && line="30 2 * * * bash $CODE_DIR/scripts/server/sites.sh backup-all >/dev/null 2>&1"
  case "$tz" in Asia/Karachi|Etc/UTC|UTC) ;; *) warn "server time zone is $tz: check the time in 'crontab -l'";; esac
  # one nightly job for every institute (it replaces the older Vertex-only line, so nothing runs twice)
  ( crontab -l 2>/dev/null | grep -v "scripts/server/backup.sh" | grep -v "scripts/server/sites.sh backup-all"; echo "$line" ) | crontab - \
    && ok "nightly backups of every institute at 2:30 AM Pakistan time"
}

# Ask for a Backblaze B2 key and bucket and save them as rclone remote $1 (checked before anything else changes).
connect_b2() {
  local rname="$1" kid key
  printf '\nFrom Backblaze (Application Keys): \nB2 keyID: '; read -r kid
  printf 'B2 applicationKey (not shown while you paste): '; read -rs key; echo
  printf 'Bucket name (exactly as created in Backblaze): '; read -r B2_BUCKET
  printf '%s' "$B2_BUCKET" | grep -Eq '^[a-z0-9][a-z0-9-]{4,48}[a-z0-9]$' || die "That bucket name does not look right (lower-case letters, digits and -). Nothing was changed."
  printf 'account=%s\nkey=%s\nhard_delete=true\n' "$kid" "$key" | rc_section "$rname" b2 || die "Could not save the B2 connection."
  unset key
  rclone_run mkdir "$rname:$B2_BUCKET" >/dev/null 2>&1 || true
  if ! rclone_run lsd "$rname:$B2_BUCKET" >/dev/null 2>&1; then
    rc_delete "$rname"
    die "B2 did not accept the key or the bucket name (check the key has Read and Write access to that bucket). Nothing was kept."
  fi
  ok "Backblaze B2 bucket $B2_BUCKET connected"
}
rc_delete() {
  NAME="$1" F="$RCLONE_DIR/rclone.conf" python3 -c '
import configparser, os
f = os.environ["F"]; c = configparser.RawConfigParser(); c.optionxform = str; c.read(f)
if c.remove_section(os.environ["NAME"]):
    with open(f, "w") as fh:
        c.write(fh)
'
}

# Move every institute's backups to Backblaze B2. Each institute keeps its encrypted folder and its two passwords:
# only where the folder lives changes. The copies already on the old account stay there (delete them later by hand).
backups_switch() {
  need_root
  [ -f "$STORE_CONF" ] || die "No backup account yet: run sites.sh backups-setup."
  local old_base; old_base="$(envget BASE "$STORE_CONF")"
  [ "$(envget PROVIDER "$STORE_CONF")" = b2 ] && die "Backups already go to Backblaze B2 ($old_base)."
  confirm "Move the backups of every institute from $old_base to Backblaze B2.
Encryption and the two backup passwords of each institute stay the same; nothing new to write down.
The copies already on the old account are left there."
  connect_b2 lmsb2
  local new_base="lmsb2:$B2_BUCKET"
  cp -p "$RCLONE_DIR/rclone.conf" "$RCLONE_DIR/rclone.conf.before-b2-$(date +%Y%m%d-%H%M%S)"
  OLD="$old_base/" NEW="$new_base/" F="$RCLONE_DIR/rclone.conf" python3 -c '
import configparser, os
f, old, new = os.environ["F"], os.environ["OLD"], os.environ["NEW"]
c = configparser.RawConfigParser(); c.optionxform = str; c.read(f)
moved = []
for s in c.sections():
    if c.get(s, "type", fallback="") == "crypt" and c.get(s, "remote", fallback="").startswith(old):
        c.set(s, "remote", new + c.get(s, "remote")[len(old):]); moved.append(s)
with open(f, "w") as fh:
    c.write(fh)
print(" ".join(moved))
' > /tmp/lms-moved
  printf 'PROVIDER=b2\nBASE=%s\n' "$new_base" > "$STORE_CONF"; chmod 600 "$STORE_CONF"
  ok "moved to B2: $(cat /tmp/lms-moved)"; rm -f /tmp/lms-moved
  local name
  for name in $(all_names); do
    say "First backup of $name on B2"
    backup_one "$name" && verify_one "$name" || warn "$name: see $(site_dir "$name")/backups/backup.log"
  done
  rc_delete "${old_base%%:*}" && ok "the old connection (${old_base%%:*}) was removed from this server"
}

# The encrypted folder for one institute: <backup account>/<name>, with its own two passwords (shown once).
site_backup_remote() {
  local name="$1" dir remote base
  dir="$(site_dir "$name")"; base="$(envget BASE "$STORE_CONF")"
  remote="$(envget BACKUP_REMOTE "$dir/.env")"
  if [ "$name" = vertex ] && [ -z "$remote" ] && rclone_run listremotes 2>/dev/null | grep -qx "vistbackup:"; then
    ok "Vertex keeps its existing encrypted Google Drive backup (vistbackup:)"; return 0
  fi
  [ -n "$remote" ] || remote="lmsbk-$name:"
  if rclone_run listremotes 2>/dev/null | grep -qx "${remote%%:*}:"; then ok "$name: encrypted folder already set up"; return 0; fi
  local p1 p2; p1="$(rnd 32)"; p2="$(rnd 32)"
  local o1 o2; o1="$(obscure "$p1")"; o2="$(obscure "$p2")"
  [ -n "$o1" ] && [ -n "$o2" ] || { warn "could not set up encryption for $name"; return 1; }
  printf 'remote=%s/%s\nfilename_encryption=standard\ndirectory_name_encryption=true\npassword=%s\npassword2=%s\n' "$base" "$name" "$o1" "$o2" \
    | rc_section "${remote%%:*}" crypt || { warn "could not set up encryption for $name"; return 1; }
  if [ "$name" = vertex ]; then cp -p "$dir/.env" "$dir/backups/env-before-backup-remote-$(date +%Y%m%d-%H%M%S)" 2>/dev/null; fi
  rclone_run mkdir "$remote" >/dev/null 2>&1 || true          # the institute's (empty) folder on the remote
  V="$remote"; setkey "$dir/.env" BACKUP_REMOTE V
  printf '\n\033[1;33m%s\033[0m\n' "WRITE DOWN the backup passwords of '$name' NOW and keep them OFF this server (password manager / office safe)."
  printf 'Without them its backups CANNOT be opened, not even by you. Shown only this once.\n\n    %s backup password 1:  %s\n    %s backup password 2:  %s\n\n' "$name" "$p1" "$name" "$p2"
  unset p1 p2
  local w=""; while [ "$w" != "WROTE" ]; do printf 'Type WROTE when you have written both down: '; read -r w; done
  clear 2>/dev/null || true
  ok "$name: encrypted folder $base/$name"
}
backup_one() { local d; d="$(site_dir "$1")"; LMS_SITE_DIR="$d" bash "$CODE_DIR/scripts/server/backup.sh" run; }
verify_one() { local d; d="$(site_dir "$1")"; LMS_SITE_DIR="$d" bash "$CODE_DIR/scripts/server/backup.sh" verify; }
backup_all() {
  exec 8>"/tmp/lms-backup-all.lock"; flock -n 8 || { echo "another backup-all is running"; exit 0; }
  local name failed=""
  for name in $(all_names); do
    [ "$name" != vertex ] && grep -qx 'ENABLED=0' "$SITES_DIR/$name/site.env" && continue
    backup_one "$name" || failed="$failed $name"
  done
  [ -z "$failed" ] || { echo "backup failed for:$failed" >&2; exit 1; }
}

case "${1:-}" in
  list) list ;;
  add) shift; add "$@" ;;
  https) need_root; https "${2:-}" || exit 1 ;;
  disable) disable "${2:-}" ;;
  enable) enable "${2:-}" ;;
  backups-setup) backups_setup ;;
  backups-switch) backups_switch ;;
  backup) need_root; [ -n "${2:-}" ] || die "Use: sites.sh backup <name|vertex>"; [ -f "$STORE_CONF" ] || [ "${2:-}" = vertex ] || die "Run sites.sh backups-setup first."; backup_one "$2" ;;
  verify) need_root; verify_one "${2:-vertex}" ;;
  backup-all) need_root; backup_all ;;
  *) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
