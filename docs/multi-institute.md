# Several institutes on one server

One VPS, one copy of the code, one GitHub pipeline, and **each institute completely separate**:

| | Vertex (today) | Every other institute |
|---|---|---|
| Folder | `/opt/vist-lms` (code + `.env`) | `/opt/lms-sites/<name>/` (`.env`, uploads, backups) |
| Database | its own, as today | its own database `lms_<name>` with its own user and password (cannot read any other) |
| Secrets | its own `.env` | its own `.env` (mode 600): new random JWT secret, push keys, database password |
| App, worker, SMS sender, Redis | project `vist-lms`, port 8000 | project `lms-<name>`, its own port (8001, 8002 …), its own Redis |
| Uploaded files | `/opt/vist-lms/var/storage` | `/opt/lms-sites/<name>/var/storage` |
| Web address | as today | its own domain, nginx site `lms-<name>`, free HTTPS (Let's Encrypt) |
| Backups | before every deploy + nightly, encrypted | the same, in its own encrypted folder with its own passwords |

Nothing about Vertex changes. Adding an institute never stops or touches Vertex or another institute.

Run every command below **on the VPS, as root**, in the SSH window. Never paste a password, a key or a `.env` into a chat.

---

## Step 0: once, after this change is merged

Merge the pull request and approve the deploy in GitHub as usual. It deploys Vertex exactly as before; the summary at
the end of the log says `Vertex: OK`.

Check:

```bash
bash /opt/vist-lms/scripts/server/sites.sh list
```

You see Vertex, its health, its memory, and "Memory free for more". One more institute needs about 900 MB. The VPS
(8 GB) comfortably holds Vertex plus 2-3 more.

## Step 1: backups (once, for every institute)

Do this before adding anyone, so every institute is protected from its first day.

**Backblaze B2 (recommended)**

1. Create an account at <https://www.backblaze.com/b2/sign-up.html> (use an e-mail only you control).
2. **B2 Cloud Storage → Buckets → Create a Bucket**: a name like `lms-backups-x7k2p9`, **Private**, encryption on.
3. **Application Keys → Add a New Application Key**: name `vps`, *Allow access to bucket*: the bucket above,
   *Type of access*: Read and Write. Copy the **keyID** and **applicationKey** (the key is shown only once).

**or Google Drive** (not recommended: rclone's built-in Google connection is shared by everyone and Google often
refuses uploads with "Quota exceeded … RATE_LIMIT_EXCEEDED"; if that happens, switch with
`sites.sh backups-switch`, see below): make a Google account used only for backups. On your Windows PC download rclone from
<https://rclone.org/downloads/>, unzip it, open PowerShell in that folder and run `.\rclone.exe authorize "drive"`,
sign in with that account, and copy the text between `--->` and `<---`.

Then on the VPS:

```bash
bash /opt/vist-lms/scripts/server/sites.sh backups-setup
```

- Choose 1 (B2) or 2 (Google Drive) and paste what it asks for (it is not shown on screen).
- For **each institute** it makes an encrypted folder and shows **two backup passwords once**. Write them down
  (password manager, or paper in the office safe) and type `WROTE`. Without them that institute's backups cannot be
  opened, not even by you; nobody else can open them either, not even Backblaze or Google.
- It makes the first backup of each institute and checks it can really be restored.
- It schedules one nightly job at **2:30 AM (Pakistan time)** for every institute. (If Vertex already had its Google
  Drive backup from `harden.sh backups`, Vertex keeps it; the old Vertex-only nightly line is replaced, so nothing runs twice.)

What a nightly backup sends: a **full copy of the database** (kept 14 days, plus 8 weekly copies), and the
**uploaded files incrementally**: one up-to-date copy, and each night only new or changed files go up. A file that was
changed or deleted on the server is kept on the backup for 30 days. So the first night uploads all files (it can take a
while), later nights only a few MB.

Each admin dashboard shows "Off-server backup: N h ago" (or "failed") for its own institute.

**Moving from Google Drive to Backblaze B2** (create the B2 bucket and key as above, then):

```bash
bash /opt/vist-lms/scripts/server/sites.sh backups-switch
```

Every institute keeps its encrypted folder and its two backup passwords (nothing new to write down); only the place
changes. It makes a fresh backup of every institute on B2 and checks it. The old copies on Google Drive stay there.

## Step 2: the new institute's domain

The institute (or you) adds one DNS record at the place where its domain is registered:

| Type | Name | Value |
|---|---|---|
| A | `lms` (for `lms.abccollege.edu.pk`) or `@` for the bare domain | the VPS's IP address |

Wait until it works (usually minutes, sometimes a few hours). Check from any PC: `nslookup lms.abccollege.edu.pk`
shows the VPS's IP.

## Step 3: add the institute

Choose a **short name**: 2-20 lower-case letters/digits, e.g. `abc`. It names the folder, the database (`lms_abc`)
and the containers; users never see it.

```bash
bash /opt/vist-lms/scripts/server/sites.sh add abc lms.abccollege.edu.pk "ABC College of Nursing"
```

It checks memory, the domain and the database server, shows what it will do, and waits for `YES`. Then:

1. Creates database `lms_abc` with its own user and a random password.
2. Creates `/opt/lms-sites/abc` with a new `.env`. Only harmless defaults are copied from Vertex (time zone, storage
   type); SMS, e-mail and 1Bill settings start empty because they belong to the institute.
3. Checks the app can reach the new database.
4. Makes its own push-notification keys.
5. Creates the tables, the roles and the first administrator `admin`.
6. Starts it on its own port and waits until it is healthy.
7. Adds the nginx site and gets the HTTPS certificate.
8. Sets up its encrypted backup folder (two passwords to write down), makes the first backup and checks it.

If any step 1-7 fails, **everything it made is removed again** (database, user, folder, nginx site), so you can fix the
cause and run the same command again. Vertex and the other institutes are never touched.

At the end it shows the **first login** (address, `admin`, a random password) once. Give it only to that institute's
administrator; they must choose a new password at first login, then set the institute name, logo, fees and SMS in
Settings.

**If it says "The app cannot log in to lms_abc"**: PostgreSQL only accepts the users it was told about. Open
`/etc/postgresql/*/main/pg_hba.conf`, find the line that lets Vertex's user in from `172.16.0.0/12` (or similar),
and add the same line for the new database and user, e.g.

```
host  lms_abc  lms_abc  172.16.0.0/12   scram-sha-256
host  lms_abc  lms_abc  192.168.0.0/16  scram-sha-256
```

then `systemctl reload postgresql` and run the `add` command again.

**If HTTPS was skipped** (the domain did not point here yet): when it does, run

```bash
bash /opt/vist-lms/scripts/server/sites.sh https abc
```

## Every deploy from now on

Merge to `main` → the checks pass → you approve "production" in GitHub → `scripts/deploy_all.sh` runs on the VPS:

1. **Vertex first**: backup, build, migrations, restart, health check. If Vertex fails, **no other institute is
   touched**; they keep running the previous version.
2. Then **each institute in turn**, using the image just built: its own backup (kept in
   `/opt/lms-sites/<name>/backups`, last 10), its own migrations, restart, health check. If one fails it keeps its
   previous version and the next one still deploys.
3. A summary at the end of the GitHub log: `OK: abc kcon` / `FAILED: …`. Any failure makes the run red.

## Day to day

```bash
bash /opt/vist-lms/scripts/server/sites.sh list            # health, memory, last backup of every institute
bash /opt/vist-lms/scripts/server/sites.sh backup abc       # back one up now
bash /opt/vist-lms/scripts/server/sites.sh verify abc       # download its newest backup and check it can be restored
bash /opt/vist-lms/scripts/server/sites.sh disable abc      # stop it (data, files and backups kept; deploys skip it)
bash /opt/vist-lms/scripts/server/sites.sh enable abc       # start it again with the current version
```

Do a `verify` of each institute once a month.

## Restoring one institute from its backup

Only that institute is affected. Ask me before doing this the first time.

1. `bash /opt/vist-lms/scripts/server/sites.sh disable abc`
2. Download the copy you need: `docker run --rm -v /root/.config/rclone:/config/rclone -v /opt/lms-sites/abc/backups:/b rclone/rclone:1.68 copy lmsbk-abc:daily/vist-db-YYYYMMDD-HHMM.dump /b/`
3. Restore it into its own database (the user and password are in `/opt/lms-sites/abc/.env`):
   `pg_restore --clean --if-exists --no-owner -d <its DATABASE_URL_SYNC without +psycopg> /opt/lms-sites/abc/backups/vist-db-….dump`
4. `bash /opt/vist-lms/scripts/server/sites.sh enable abc`

## Removing an institute for good

There is deliberately no one-line command. Make a final backup and verify it, then: `disable`, remove
`/etc/nginx/sites-enabled/lms-<name>.conf` and reload nginx, `docker compose -p lms-<name> down -v`, drop its database
and user, and delete `/opt/lms-sites/<name>`. Keep its backup passwords as long as you keep its backups.
