# Get an email when the portal goes down

A free outside service checks the portal every 5 minutes and emails you when it stops answering, and again when it is
back. It runs on its own servers, so it still works when ours is down (which a check on our own server could not).

## Set up (about 2 minutes, once)

1. Go to **uptimerobot.com** → *Register for FREE* → sign up with the email that should receive the alerts
   (use the institute's admin email, so it does not depend on one person).
2. Click **+ New monitor** and fill in:
   - **Monitor type:** HTTP(s)
   - **Friendly name:** VIST portal
   - **URL:** `https://<your-portal-address>/health/ready`
     (today: `https://187-127-220-153.sslip.io/health/ready`; change it when you move to your own domain)
   - **Monitoring interval:** 5 minutes
   - **Alert contacts:** tick your email
3. Click **Create monitor**. Within a few minutes it shows **Up** (green).

`/health/ready` answers "OK" only when the website, the records database and the background services all work, and when
the database is up to date with the app. So you also hear about a half-broken site, not only a fully stopped one.

## When an alert comes

1. Open the portal. If it does not load, sign in to the VPS and run:
   ```
   cd /opt/vist-lms && docker compose -f docker-compose.prod.yml ps
   ```
2. Anything not "Up / healthy": `docker compose -f docker-compose.prod.yml up -d` starts it again.
3. Still down: send the output of `docker compose -f docker-compose.prod.yml logs --tail 80 app` to your developer.

Optional: *Status pages* in UptimeRobot gives a public page showing the portal's uptime history.
