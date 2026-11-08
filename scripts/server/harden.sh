#!/usr/bin/env bash
# Server safety steps for the VIST LMS VPS. Run ON THE VPS, from /opt/vist-lms, as root, one step at a time, at a quiet hour.
# Every step: says what it will do and waits for YES, keeps a copy of .env, checks the app is healthy afterwards and
# puts everything back by itself if it is not. Passwords and keys are never printed.
#
#   bash scripts/server/harden.sh check                 what is open / weak now (changes nothing)
#   bash scripts/server/harden.sh firewall [--allow-ip 1.2.3.4]
#                                                       close the database port (5432) to the internet; the app keeps working
#   bash scripts/server/harden.sh firewall-undo         remove that rule again
#   bash scripts/server/harden.sh rotate-db-password    new database password (in .env only)
#   bash scripts/server/harden.sh rotate-jwt            new JWT_SECRET: everyone signs in again once; SMTP password kept
#   bash scripts/server/harden.sh rotate-vapid          new push keys: phones re-subscribe by themselves on next open
#   bash scripts/server/harden.sh rename-db [new_name]  rename the database (default vist_lms); about 1 minute offline
#   bash scripts/server/harden.sh disk                  free disk space: Docker build leftovers + cap system logs at 200 MB
#   bash scripts/server/harden.sh backups               nightly encrypted backups to Google Drive (asks for the token from your PC)
#   bash scripts/server/harden.sh all                   ALL of the above in a safe order, one step at a time (YES each, or skip)
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

ENV_FILE=".env"
COMPOSE="docker compose -f docker-compose.prod.yml"
PG_IMAGE="postgres:18-alpine"
SERVICES="app worker sms"
UNIT="/etc/systemd/system/vist-db-firewall.service"
RULES="/usr/local/sbin/vist-db-firewall"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[1;31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }
envval() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
confirm() {
  printf '\n%s\nType YES to go ahead%s: ' "$1" "${IN_ALL:+ (anything else skips this step)}"; read -r a
  [ "$a" = "YES" ] && return 0
  [ -n "${IN_ALL:-}" ] && { printf 'Skipped. Nothing was changed.\n'; exit 3; }
  die "Nothing was changed."
}
need_root() { [ "$(id -u)" = "0" ] || die "Run as root (sudo)."; }

[ -f "$ENV_FILE" ] || die ".env not found. Run from /opt/vist-lms."
command -v python3 >/dev/null || die "python3 is needed on the server (apt install python3)."

# Edit .env with python (never echoes values). setenv KEY VALUE-from-env-var-NAME
setenv() {
  KEY="$1" VALVAR="$2" python3 - "$ENV_FILE" <<'PY'
import os, sys
path, key, val = sys.argv[1], os.environ["KEY"], os.environ[os.environ["VALVAR"]]
lines = open(path).read().splitlines()
out, done = [], False
for ln in lines:
    if ln.startswith(key + "="):
        if not done:
            out.append(f"{key}={val}"); done = True
        continue
    out.append(ln)
if not done:
    out.append(f"{key}={val}")
open(path, "w").write("\n".join(out) + "\n")
PY
}
# Rewrite the password or the database name inside both DATABASE_URL lines. urlpart password|dbname VALUE-VAR
urlpart() {
  PART="$1" VALVAR="$2" python3 - "$ENV_FILE" <<'PY'
import os, sys
from urllib.parse import urlsplit, urlunsplit, quote
path, part, val = sys.argv[1], os.environ["PART"], os.environ[os.environ["VALVAR"]]
lines = open(path).read().splitlines()
for i, ln in enumerate(lines):
    for key in ("DATABASE_URL=", "DATABASE_URL_SYNC="):
        if ln.startswith(key):
            raw = ln[len(key):].strip().strip("'\"")
            u = urlsplit(raw)
            if part == "password":
                host = u.netloc.rsplit("@", 1)[1]
                user = u.netloc.rsplit("@", 1)[0].split(":", 1)[0]
                u = u._replace(netloc=f"{user}:{quote(val, safe='')}@{host}")
            else:
                u = u._replace(path="/" + val)
            lines[i] = key + urlunsplit(u)
open(path, "w").write("\n".join(lines) + "\n")
PY
}
pgurl() { envval DATABASE_URL_SYNC | sed 's#^postgresql+psycopg://#postgresql://#'; }
# psql against the database in .env (or another database on the same server with $1 = its name). SQL on stdin.
psql_db() {
  local url; url="$(pgurl)"
  [ -n "${1:-}" ] && url="$(URL="$url" DB="$1" python3 -c 'import os;from urllib.parse import urlsplit,urlunsplit;u=urlsplit(os.environ["URL"]);print(urlunsplit(u._replace(path="/"+os.environ["DB"])))')"
  docker run --rm -i -e PGURL="$url" "$PG_IMAGE" sh -c 'psql "$PGURL" -v ON_ERROR_STOP=1 -qtA'
}
keep_env() { STAMP="$(date +%Y%m%d-%H%M%S)"; cp -p "$ENV_FILE" "backups/env-before-$1-$STAMP"; chmod 600 "backups/env-before-$1-$STAMP"; ENV_COPY="backups/env-before-$1-$STAMP"; }
restore_env() { cp -p "$ENV_COPY" "$ENV_FILE"; }
healthy() {
  for _ in $(seq 1 45); do curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1 && return 0; sleep 2; done
  return 1
}
recreate() { $COMPOSE up -d --force-recreate --no-deps $SERVICES >/dev/null 2>&1; }
db_dump() {
  say "Safety copy of the database first (backups/before-$1-*.dump)"
  docker run --rm -e PGURL="$(pgurl)" -v "$PWD/backups:/b" "$PG_IMAGE" sh -c 'pg_dump --format=custom --no-owner "$PGURL" -f /b/before-'"$1"'-'"$(date +%Y%m%d-%H%M%S)"'.dump' \
    || die "The safety copy failed, so nothing was changed."
  ok "safety copy made"
}
mkdir -p backups; chmod 700 backups

# ------------------------------------------------------------------ check
check() {
  say "Database connection in .env"
  local url host port db
  url="$(pgurl)"
  host="$(URL="$url" python3 -c 'import os;from urllib.parse import urlsplit;print(urlsplit(os.environ["URL"]).hostname)')"
  port="$(URL="$url" python3 -c 'import os;from urllib.parse import urlsplit;print(urlsplit(os.environ["URL"]).port or 5432)')"
  db="$(URL="$url" python3 -c 'import os;from urllib.parse import urlsplit;print(urlsplit(os.environ["URL"]).path.lstrip("/"))')"
  echo "    server: $host  port: $port  database: $db"
  case "$db" in *test*|*sandbox*) warn "the live database is named '$db': consider 'rename-db' (deploy.sh refuses names with _test)";; *) ok "database name";; esac

  say "Who can reach port 5432"
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:)5432$'; then
    ss -ltn | awk 'NR==1 || $4 ~ /:5432$/'
    if ss -ltn | awk '{print $4}' | grep -Eq '^(0\.0\.0\.0|\*|\[::\]):5432$'; then warn "Postgres listens on ALL addresses of this server"; fi
  else
    echo "    nothing listens on 5432 on the host itself"
  fi
  docker ps --format '{{.Names}}  {{.Ports}}' 2>/dev/null | grep -E '5432' | sed 's/^/    container: /' || true
  if [ -f "$UNIT" ] && systemctl is-active --quiet vist-db-firewall; then ok "the database firewall rule is on (vist-db-firewall)"
  else warn "the database port is not firewalled by this script yet: run 'firewall'"; fi

  say "Secrets in .env (lengths only)"
  local j; j="$(envval JWT_SECRET)"; [ "${#j}" -ge 48 ] && ok "JWT_SECRET is ${#j} characters" || warn "JWT_SECRET is short (${#j}); run rotate-jwt"
  [ -n "$(envval VAPID_PRIVATE_KEY)" ] && ok "push keys are set" || warn "push keys are not set"
  say "Backups"
  [ -s /root/.config/rclone/rclone.conf ] && ok "rclone is set up" || warn "off-server backups are not set up (docs/server-safety.md step 2)"
  crontab -l 2>/dev/null | grep -q "scripts/server/backup.sh" && ok "nightly backup is scheduled" || warn "no nightly backup in root's crontab"
  echo
}

# ------------------------------------------------------------------ firewall for 5432
firewall() {
  need_root
  local allow=""
  [ "${1:-}" = "--allow-ip" ] && { allow="${2:-}"; [ -n "$allow" ] || die "Give the address after --allow-ip"; }
  confirm "Block port 5432 for everyone on the internet. Still allowed: this server itself, the app's Docker containers${allow:+, and $allow}.
To use pgAdmin from your PC afterwards, connect through an SSH tunnel (see the guide)."
  cat > "$RULES" <<EOF
#!/bin/sh
# VIST LMS: database port 5432 only from this server, its Docker networks${allow:+ and $allow}. Made by scripts/server/harden.sh.
for T in iptables ip6tables; do
  command -v \$T >/dev/null || continue
  \$T -N VIST-DB 2>/dev/null; \$T -F VIST-DB
  \$T -A VIST-DB -i lo -j RETURN
  \$T -A VIST-DB -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
done
iptables -A VIST-DB -s 172.16.0.0/12 -j RETURN
iptables -A VIST-DB -s 192.168.0.0/16 -j RETURN
iptables -A VIST-DB -s 10.0.0.0/8 -j RETURN
${allow:+iptables -A VIST-DB -s $allow -j RETURN}
for T in iptables ip6tables; do
  command -v \$T >/dev/null || continue
  \$T -A VIST-DB -j DROP
  \$T -C INPUT -p tcp --dport 5432 -j VIST-DB 2>/dev/null || \$T -I INPUT 1 -p tcp --dport 5432 -j VIST-DB
done
# A database published by Docker (-p 5432:5432) bypasses INPUT: guard it in DOCKER-USER too.
if iptables -L DOCKER-USER -n >/dev/null 2>&1; then
  iptables -C DOCKER-USER -p tcp -m conntrack --ctorigdstport 5432 --ctdir ORIGINAL -j VIST-DB 2>/dev/null \\
    || iptables -I DOCKER-USER 1 -p tcp -m conntrack --ctorigdstport 5432 --ctdir ORIGINAL -j VIST-DB
fi
EOF
  chmod 700 "$RULES"
  cat > "$UNIT" <<EOF
[Unit]
Description=VIST LMS: close the database port to the internet
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$RULES

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload && systemctl enable --now vist-db-firewall >/dev/null 2>&1 || { firewall_undo quiet; die "The rule could not be applied; it was removed again."; }
  say "Checking the app still reaches the database"
  if $COMPOSE restart app >/dev/null 2>&1 && healthy; then
    ok "port 5432 is closed to the internet and the app is healthy"
    echo "    From your PC (PowerShell):  Test-NetConnection <server-ip> -Port 5432   should now say TcpTestSucceeded : False"
  else
    firewall_undo quiet; $COMPOSE restart app >/dev/null 2>&1
    die "The app could not reach the database with the rule on, so the rule was removed (nothing else changed). Send me the output of 'check'."
  fi
}
firewall_undo() {
  need_root
  systemctl disable --now vist-db-firewall >/dev/null 2>&1 || true
  for T in iptables ip6tables; do
    command -v $T >/dev/null || continue
    while $T -D INPUT -p tcp --dport 5432 -j VIST-DB 2>/dev/null; do :; done
    $T -F VIST-DB 2>/dev/null; $T -X VIST-DB 2>/dev/null || true
  done
  while iptables -D DOCKER-USER -p tcp -m conntrack --ctorigdstport 5432 --ctdir ORIGINAL -j VIST-DB 2>/dev/null; do :; done
  iptables -F VIST-DB 2>/dev/null; iptables -X VIST-DB 2>/dev/null || true
  rm -f "$UNIT" "$RULES"; systemctl daemon-reload
  [ "${1:-}" = "quiet" ] || ok "the database firewall rule was removed"
}

# ------------------------------------------------------------------ new database password
rotate_db_password() {
  local user
  user="$(URL="$(pgurl)" python3 -c 'import os;from urllib.parse import urlsplit;print(urlsplit(os.environ["URL"]).username)')"
  confirm "Give the database user '$user' a new random password and put it in .env (the app restarts, ~20 s).
Anything else that signs in as '$user' (pgAdmin on your PC) will need the new password from .env."
  keep_env dbpass
  OLD_PW="$(URL="$(pgurl)" python3 -c 'import os;from urllib.parse import urlsplit,unquote;print(unquote(urlsplit(os.environ["URL"]).password or ""))')"
  NEW_PW="$(openssl rand -hex 24)"
  export OLD_PW NEW_PW
  printf 'ALTER ROLE "%s" PASSWORD %s;\n' "$user" "'$NEW_PW'" | psql_db >/dev/null || die "The database refused the change; nothing was changed."
  urlpart password NEW_PW
  say "Restarting the app with the new password"
  if recreate && healthy; then
    ok "new database password is in use (copy of the old .env: $ENV_COPY; delete it once all is well)"
  else
    warn "The app did not come back: putting the old password back"
    restore_env
    printf 'ALTER ROLE "%s" PASSWORD %s;\n' "$user" "'$OLD_PW'" | docker run --rm -i -e PGURL="$(URL="$(pgurl)" NEWPW="$NEW_PW" python3 -c 'import os;from urllib.parse import urlsplit,urlunsplit,quote;u=urlsplit(os.environ["URL"]);h=u.netloc.rsplit("@",1);print(urlunsplit(u._replace(netloc=h[0].split(":",1)[0]+":"+quote(os.environ["NEWPW"],safe="")+"@"+h[1])))')" "$PG_IMAGE" sh -c 'psql "$PGURL" -qtA' >/dev/null
    recreate; healthy && die "Put back as it was; the app is healthy with the old password." || die "Put back, but the app is still not healthy: run '$COMPOSE logs --tail=80 app'."
  fi
}

# ------------------------------------------------------------------ new JWT secret
rotate_jwt() {
  confirm "Make a new JWT_SECRET. Effects: everyone is signed out once and signs in again; password-reset codes sent in the
last few minutes stop working. The stored SMTP password is re-encrypted with the new secret, so email keeps working."
  keep_env jwt
  NEW_JWT_SECRET="$(openssl rand -hex 48)"; export NEW_JWT_SECRET
  say "Re-encrypting stored secrets for the new key"
  $COMPOSE run --rm --no-deps -e NEW_JWT_SECRET app python scripts/reseal_secrets.py || die "Re-encrypting failed; nothing was changed."
  setenv JWT_SECRET NEW_JWT_SECRET
  say "Restarting the app with the new secret"
  if recreate && healthy; then
    ok "new JWT_SECRET is in use; everyone signs in again (copy of the old .env: $ENV_COPY)"
  else
    warn "The app did not come back: putting the old secret back"
    restore_env
    $COMPOSE run --rm --no-deps -e NEW_JWT_SECRET app python scripts/reseal_secrets.py --back >/dev/null 2>&1
    recreate; healthy && die "Put back as it was." || die "Put back, but the app is still not healthy: run '$COMPOSE logs --tail=80 app'."
  fi
}

# ------------------------------------------------------------------ new push keys
rotate_vapid() {
  confirm "Make new push-notification keys (the old private key was seen in a screenshot). Phones and browsers that had
notifications on switch to the new key by themselves the next time the app is opened; nobody has to do anything."
  keep_env vapid
  local out; out="$($COMPOSE run --rm --no-deps app python scripts/push_keys.py 2>/dev/null)" || die "Could not make keys; nothing was changed."
  VAPID_PUB="$(printf '%s\n' "$out" | sed -n 's/^VAPID_PUBLIC_KEY=//p')"
  VAPID_PRIV="$(printf '%s\n' "$out" | sed -n 's/^VAPID_PRIVATE_KEY=//p')"
  [ -n "$VAPID_PUB" ] && [ -n "$VAPID_PRIV" ] || die "Could not read the new keys; nothing was changed."
  export VAPID_PUB VAPID_PRIV
  setenv VAPID_PUBLIC_KEY VAPID_PUB; setenv VAPID_PRIVATE_KEY VAPID_PRIV
  [ -n "$(envval VAPID_SUBJECT)" ] || { VS="mailto:admin@localhost"; export VS; setenv VAPID_SUBJECT VS; warn "VAPID_SUBJECT was empty; set it to a real mailto: address in .env"; }
  if recreate && healthy; then ok "new push keys are in use (copy of the old .env: $ENV_COPY)"
  else restore_env; recreate; die "The app did not come back, so the old keys were put back."; fi
}

# ------------------------------------------------------------------ rename the database
rename_db() {
  local new="${1:-vist_lms}" old
  old="$(URL="$(pgurl)" python3 -c 'import os;from urllib.parse import urlsplit;print(urlsplit(os.environ["URL"]).path.lstrip("/"))')"
  [ "$old" != "$new" ] || die "The database is already called $new."
  printf '%s' "$new" | grep -Eq '^[a-z_][a-z0-9_]{2,40}$' || die "Use lower-case letters, digits and _ only."
  confirm "Rename the database '$old' to '$new'. The app, worker and SMS sender stop for about a minute (do it at a quiet hour).
A safety copy of the database is made first."
  db_dump rename
  keep_env rename
  say "Stopping the app, worker and SMS sender"
  $COMPOSE stop $SERVICES >/dev/null 2>&1
  OLD_DB="$old"; NEW_DB="$new"; export OLD_DB NEW_DB
  if printf "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '%s' AND pid <> pg_backend_pid();\nALTER DATABASE \"%s\" RENAME TO \"%s\";\n" "$old" "$old" "$new" | psql_db postgres >/dev/null; then
    urlpart dbname NEW_DB
  else
    $COMPOSE up -d $SERVICES >/dev/null 2>&1
    die "The database could not be renamed (someone else may be connected, e.g. pgAdmin). Nothing was changed; the app is starting again."
  fi
  say "Starting again with the new name"
  if recreate && healthy; then
    ok "the database is now called '$new' and the app is healthy (copy of the old .env: $ENV_COPY)"
    echo "    If pgAdmin or anything else on your PC connects to '$old', change it to '$new'."
  else
    warn "The app did not come back: renaming back"
    $COMPOSE stop $SERVICES >/dev/null 2>&1
    printf "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '%s' AND pid <> pg_backend_pid();\nALTER DATABASE \"%s\" RENAME TO \"%s\";\n" "$new" "$new" "$old" | psql_db postgres >/dev/null
    restore_env; recreate
    healthy && die "Put back as it was." || die "Put back, but the app is still not healthy: run '$COMPOSE logs --tail=80 app'."
  fi
}

# ------------------------------------------------------------------ disk space
disk() {
  need_root
  say "Disk now"; df -h / | tail -1; docker system df 2>/dev/null || true
  confirm "This deletes Docker build leftovers (not the running app, not data, not uploads) and caps the system journal
at 200 MB (older system log lines are dropped). The app keeps running."
  docker builder prune -af || warn "could not clear the build cache"
  docker image prune -f >/dev/null 2>&1 || true
  mkdir -p /etc/systemd/journald.conf.d
  printf '[Journal]\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/vist-size.conf
  systemctl restart systemd-journald && journalctl --vacuum-size=200M >/dev/null 2>&1 || warn "journal cap not applied"
  say "Disk after"; df -h / | tail -1
  healthy && ok "app is healthy" || warn "app health check failed: run '$COMPOSE ps'"
}

# ------------------------------------------------------------------ off-server backups (Google Drive, encrypted)
RCLONE_IMAGE="rclone/rclone:1.68"
RCLONE_DIR="/root/.config/rclone"
rclone_run() { docker run --rm -i -v "$RCLONE_DIR:/config/rclone" "$RCLONE_IMAGE" "$@"; }
backups() {
  need_root
  mkdir -p "$RCLONE_DIR"; chmod 700 "$RCLONE_DIR"
  if rclone_run listremotes 2>/dev/null | grep -qx "vistbackup:"; then
    ok "the encrypted Google Drive backup is already set up"
  else
    confirm "Set up nightly backups to Google Drive, ENCRYPTED (Google sees only scrambled files).
Before typing YES, on your Windows PC: download rclone from https://rclone.org/downloads/ , unzip it, open
PowerShell in that folder and run:   .\rclone.exe authorize \"drive\"
Sign in with the INSTITUTE's Google account. Copy the text between ---> and <--- (it starts with {\"access_token\")."
    printf '\nPaste that text here and press Enter (it is not shown): '
    local token; read -rs token; echo
    case "$token" in *access_token*refresh_token*) ;; *) die "That does not look like the token (it should start with {\"access_token\"). Nothing was changed.";; esac
    rclone_run config create gdrive drive scope=drive config_is_local=false "token=$token" --non-interactive >/dev/null \
      || die "Could not save the Google Drive connection. Nothing else was changed."
    rclone_run lsd gdrive: >/dev/null 2>&1 || { rclone_run config delete gdrive >/dev/null 2>&1; die "Google Drive did not accept the token (it may have expired: run authorize again). Nothing was kept."; }
    ok "Google Drive is connected"
    local p1 p2
    p1="$(openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-32)"; p2="$(openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-32)"
    rclone_run config create vistbackup crypt remote=gdrive:VIST-LMS-backups filename_encryption=standard \
      directory_name_encryption=true "password=$p1" "password2=$p2" --obscure --non-interactive >/dev/null \
      || die "Could not set up encryption. Google Drive stays connected; run 'backups' again."
    printf '\n\033[1;33m%s\033[0m\n' "WRITE THESE TWO PASSWORDS DOWN NOW and keep them OFF this server (password manager, or paper in the office safe)."
    printf 'Without them the backups CANNOT be opened, not even by you. They are shown only this once.\n\n'
    printf '    backup password 1:  %s\n    backup password 2:  %s\n\n' "$p1" "$p2"
    local w=""; while [ "$w" != "WROTE" ]; do printf 'Type WROTE when you have written both down: '; read -r w; done
    clear 2>/dev/null || true
    ok "encryption is set up"
  fi
  say "First backup now (database + uploaded files)"
  bash scripts/server/backup.sh || die "The backup failed: see backups/backup.log (send me the last lines)."
  say "Checking the copy on Drive can really be restored"
  bash scripts/server/backup.sh verify || die "The copy could not be restored: see backups/backup.log."
  local line tz
  tz="$(timedatectl show -p Timezone --value 2>/dev/null || echo Etc/UTC)"
  case "$tz" in Asia/Karachi) line="30 2 * * * cd $PWD && bash scripts/server/backup.sh >/dev/null 2>&1" ;;
                Etc/UTC|UTC)  line="30 21 * * * cd $PWD && bash scripts/server/backup.sh >/dev/null 2>&1" ;;
                *) warn "server time zone is $tz: add the nightly line by hand (docs/server-safety.md 2c)"; return 0 ;; esac
  if crontab -l 2>/dev/null | grep -q "scripts/server/backup.sh"; then ok "nightly backup was already scheduled"
  else (crontab -l 2>/dev/null; echo "$line") | crontab - && ok "nightly backup scheduled for 2:30 AM Pakistan time"; fi
}

# ------------------------------------------------------------------ everything, in a safe order
STATE="backups/harden-done"
all() {
  need_root
  touch "$STATE"; chmod 600 "$STATE"
  say "Guided set-up. Each step explains itself and waits for YES; anything else skips it. Safe to run again:
finished steps are skipped. If a step fails it puts things back and the run stops there."
  check
  local step rc
  # backups first, so everything after has a fresh off-server copy; the JWT change (signs everyone out) last
  for step in disk backups firewall rotate_db_password rotate_vapid rotate_jwt; do
    if grep -qx "$step" "$STATE"; then ok "$step: done before"; continue; fi
    say "Step: $step"
    if [ "$step" = firewall ]; then
      printf 'Do you use pgAdmin (or anything else) from your office/home PC on port 5432? Type its public IP to keep it\nallowed, or just press Enter: '
      local ip; read -r ip
      ( IN_ALL=1; firewall ${ip:+--allow-ip "$ip"} ); rc=$?
    else
      ( IN_ALL=1; "$step" ); rc=$?
    fi
    case $rc in
      0) echo "$step" >> "$STATE" ;;
      3) warn "$step skipped; run 'all' again later to do it" ;;
      *) die "Stopped at $step (see above). The earlier steps stay done; fix this and run 'all' again." ;;
    esac
  done
  say "Finished"; check
}

case "${1:-}" in
  check) check ;;
  firewall) shift; firewall "$@" ;;
  firewall-undo) firewall_undo ;;
  rotate-db-password) rotate_db_password ;;
  rotate-jwt) rotate_jwt ;;
  rotate-vapid) rotate_vapid ;;
  rename-db) shift; rename_db "$@" ;;
  disk) disk ;;
  backups) backups ;;
  all) all ;;
  *) sed -n '2,18p' "$0"; exit 2 ;;
esac
