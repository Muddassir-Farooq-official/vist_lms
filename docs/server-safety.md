# Server safety: backups, firewall, new secrets

Run everything **on the VPS**, as root, from `/opt/vist-lms`, at a quiet hour (evening or early morning).
Do the steps **in order, one at a time**. Each step says what it will do, waits for you to type `YES`, and keeps a copy of `.env` in `backups/`.
If the app is not healthy afterwards, it puts everything back by itself.
None of these steps prints a password or a key.

```bash
ssh root@<your-server>
cd /opt/vist-lms
```

---

## Step 1: See what is open now (changes nothing)

```bash
bash scripts/server/harden.sh check
```

The report shows:

- where the database is;
- whether port 5432 is open to the internet;
- whether the database is still called `vist_lms_test`;
- whether backups are set up.

Keep this output in case you need help later.

---

## Step 2: Nightly backups to Google Drive

### 2a. Install the Google Drive connection (once)

The backup uses **rclone** (it runs in Docker, so nothing is installed).
Google sign-in needs a browser, so the sign-in happens on **your Windows PC**.

1. **On your PC:** download rclone for Windows from https://rclone.org/downloads/ and unzip it. Open PowerShell in that folder and run:

   ```powershell
   .\rclone.exe authorize "drive"
   ```

   Sign in with the Google account that should hold the backups.
   PowerShell then prints a long token between `--->` and `<---`. Copy it, and **do not share it**.

2. **On the server:**

   ```bash
   mkdir -p /root/.config/rclone
   docker run --rm -it -v /root/.config/rclone:/config/rclone rclone/rclone:1.68 config
   ```

   Answer the questions like this:

   | Question | Answer |
   |---|---|
   | New remote | `n` |
   | name | `gdrive` |
   | Storage | `drive` (Google Drive) |
   | client_id / client_secret | press Enter (leave empty) |
   | scope | `1` (full access) |
   | service_account_file | Enter |
   | Edit advanced config | `n` |
   | Use web browser to automatically authenticate? | **`n`** |
   | config_token | paste the token from your PC |
   | Shared Drive | `n` |
   | Keep this remote | `y` |

3. **Make it encrypted (strongly recommended).** The backup contains student CNICs, phone numbers and fees.
   With encryption, Google (or anyone who opens the Drive folder) sees only scrambled files.

   Still inside `config`, add a second remote:

   | Question | Answer |
   |---|---|
   | New remote | `n` |
   | name | `vistbackup` |
   | Storage | `crypt` |
   | remote | `gdrive:VIST-LMS-backups` |
   | filename_encryption | `1` (standard) |
   | directory_name_encryption | `1` (true) |
   | Password | `y` and type your own long password, **or** `g` to generate one |
   | Salt password | `g` (generate) |

   Then choose `q` to quit.

   > **Write both passwords down and keep them somewhere safe off the server** (a password manager, or paper in the office safe).
   > Without them the backups **cannot be opened**, not even by you.

   If you skip encryption, add this line to `.env`: `BACKUP_REMOTE=gdrive:VIST-LMS-backups`.

### 2b. First backup by hand

```bash
bash scripts/server/backup.sh
```

The last line should read `Backup OK: database …M + files copied to vistbackup:`.

Then check that the copy on Drive can really be restored:

```bash
bash scripts/server/backup.sh verify
```

This should print `Verify OK: … readable (N tables with data)`.

### 2c. Every night at 2:30 AM Pakistan time

First check the server's time zone:

```bash
timedatectl | grep "Time zone"
```

Then open root's crontab:

```bash
crontab -e
```

Add **one** line, depending on the time zone you saw.

If the time zone is `Etc/UTC`:

```
30 21 * * * cd /opt/vist-lms && bash scripts/server/backup.sh >/dev/null 2>&1
```

If the time zone is `Asia/Karachi`:

```
30 2 * * * cd /opt/vist-lms && bash scripts/server/backup.sh >/dev/null 2>&1
```

### What you get

- **On Drive:** the last 14 nightly copies, plus 8 weekly copies (one each Sunday). Older copies are removed automatically.
- **On the server:** the last 3 copies in `backups/nightly/`.
- **On the admin dashboard,** under Background services: *Off-server backup – 5 h ago*.
  - If a backup fails, it shows the reason in red.
  - If no backup has run for 36 hours, it warns too.
- **Log:** `backups/backup.log`.

Once a month, run `bash scripts/server/backup.sh verify` to check that the backups can still be opened.

### Restoring (only if the database is lost)

Ask for help before restoring. The outline is:

1. Download a copy:

   ```bash
   docker run --rm -v /root/.config/rclone:/config/rclone -v $PWD/backups:/backups rclone/rclone:1.68 copy vistbackup:daily/<file>.dump /backups/
   ```

2. Restore it into an **empty** database with `pg_restore --no-owner -d <url> backups/<file>.dump`.
3. Unpack `vist-files-….tar.gz` into `var/`.

---

## Step 3: Close the database port to the internet

```bash
bash scripts/server/harden.sh firewall
```

After this step:

- Only this server and the app's Docker containers can reach port 5432. Anyone else on the internet is blocked.
- The script restarts the app and checks that it is healthy. If it is not, the rule is removed straight away.
- The rule is applied again automatically after every reboot.

**Check from your PC** (PowerShell):

```powershell
Test-NetConnection <server-ip> -Port 5432
```

It should show `TcpTestSucceeded : False`.

**If you use pgAdmin from your PC,** connect through SSH. In pgAdmin, open the server's properties and set:

- **SSH Tunnel** tab: Use SSH tunneling = on, Tunnel host = your server, Username = root, plus your SSH password or key.
- **Connection** tab: Host = `127.0.0.1`.

If you really need direct access from a fixed office IP instead:

```bash
bash scripts/server/harden.sh firewall --allow-ip <office-ip>
```

To remove the rule:

```bash
bash scripts/server/harden.sh firewall-undo
```

---

## Step 4: New database password

The old password was pasted in a chat, so replace it:

```bash
bash scripts/server/harden.sh rotate-db-password
```

- The new password is only in `.env`. The app restarts, which takes about 20 seconds.
- pgAdmin needs the new password. Read it on the server with `grep DATABASE_URL_SYNC .env`.

---

## Step 5: New JWT secret

```bash
bash scripts/server/harden.sh rotate-jwt
```

- **Everyone is signed out once** and signs in again.
- The saved email (SMTP) password is re-encrypted, so email keeps working.

---

## Step 6: New push-notification keys

```bash
bash scripts/server/harden.sh rotate-vapid
```

Phones and browsers that had notifications on switch to the new key **by themselves** the next time the app is opened. Nobody has to do anything.

---

## Step 7: Rename the database (`vist_lms_test` to `vist_lms`)

```bash
bash scripts/server/harden.sh rename-db
```

- The app is **offline for about one minute**.
- A safety copy of the database is made first, in `backups/before-rename-….dump`.
- Close pgAdmin first: an open connection stops the rename. In that case nothing changes and the app starts again.
- This also fixes the deploy check: `deploy.sh` refuses database names containing `_test`.

---

## Afterwards

- Each step leaves a copy of the old `.env` as `backups/env-before-…`. When everything has worked for a few days, delete them:

  ```bash
  rm backups/env-before-*
  ```

- Run `bash scripts/server/harden.sh check` again. Everything should show **OK**.

---

## Two-step sign-in for administrators

After their password, administrators type a 6-digit code from an authenticator app (Google Authenticator or
Microsoft Authenticator) on their phone. The first time, the sign-in page shows a QR code to scan.

- **Lost or changed phone:** another administrator opens **Activity log → Security → Two-step sign-in** and clicks
  **Reset (lost phone)**. The person sets it up again at their next sign-in.
- **No other administrator can do it** (e.g. the only administrator lost their phone): on the server,

  ```bash
  cd /opt/vist-lms
  docker compose -f docker-compose.prod.yml exec app python scripts/reset_2fa.py <username>
  ```

- **Turn the requirement off for a while:** Settings → Sign-in security → "Administrators must use two-step sign-in".
  Everyone's set-up is kept for when it is turned back on.

Every set-up, wrong code and reset is in the activity log.
