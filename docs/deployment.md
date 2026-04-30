# Production Deployment Guide

This guide walks a fresh DigitalOcean Droplet to a running production
restaurant POS in roughly 30 minutes. The target stack is **systemd +
nginx + native packages** (no Docker). Follow the phases in order.

All commands assume you are logged in to the Droplet as root for
Phase A and as `deploy` from Phase B onwards. Paths are anchored at
`/home/deploy/ecommerce` — adjust if you deploy somewhere else.

---

## Prerequisites

- DigitalOcean Droplet
  - Ubuntu 22.04 LTS
  - 2 GB RAM minimum (smaller will OOM under Postgres + Redis +
    3 gunicorn workers + 2 celery workers)
  - Public IPv4
- Domain name with an A record pointing to the Droplet's IP
- SSH key access as root (paste your public key during Droplet creation)

---

## Phase A — Server provisioning

Log in as root the first time:

```bash
ssh root@<droplet-ip>
```

Clone the repo so you can run the install script:

```bash
git clone https://github.com/<your-org>/ecommerce.git /tmp/ecommerce
cd /tmp/ecommerce
bash deployment/scripts/install.sh
```

`install.sh` does:

1. `apt update && apt upgrade -y`
2. Installs Python 3.12, Postgres, Redis, nginx, certbot, ufw,
   fail2ban, build deps for psycopg2 / Pillow / reportlab
3. Configures the firewall (only SSH + HTTP/HTTPS allowed inbound)
4. Enables unattended security updates and fail2ban
5. Creates the `deploy` user, copies your root authorized_keys to it
6. Initialises Postgres with the `ecommerce` user/database (with a
   placeholder password)
7. Configures Redis to bind localhost only with a placeholder password

When the script finishes, **rotate the placeholder credentials**:

```bash
# Strong Postgres password.
sudo -u postgres psql -c "ALTER USER ecommerce WITH PASSWORD 'STRONG_PG_PASSWORD';"

# Strong Redis password.
sudo sed -i 's/CHANGE_ME_DURING_INSTALL/STRONG_REDIS_PASSWORD/' /etc/redis/redis.conf
sudo systemctl restart redis-server
```

You can now log out as root and continue as `deploy`.

```bash
exit
ssh deploy@<droplet-ip>
```

---

## Phase B — App deployment

Clone the repo into the deploy user's home directory:

```bash
git clone https://github.com/<your-org>/ecommerce.git ~/ecommerce
cd ~/ecommerce
```

Create the `.env` file from the template and fill in the production
values:

```bash
cp .env.example .env
nano .env
```

Required values:

| Var | Notes |
|---|---|
| `DJANGO_SECRET_KEY` | Generate with `python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'` |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ENV` | `production` |
| `DJANGO_ALLOWED_HOSTS` | `yourdomain.com,www.yourdomain.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://yourdomain.com,https://www.yourdomain.com` |
| `DATABASE_URL` | `postgres://ecommerce:STRONG_PG_PASSWORD@localhost:5432/ecommerce` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Match the values in DATABASE_URL — `backup.sh` reads these. |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | `redis://:STRONG_REDIS_PASSWORD@localhost:6379/0` |
| `EMAIL_*` | Gmail app password and from-address |

Install the systemd units (one-time, requires sudo):

```bash
sudo cp deployment/systemd/*.service deployment/systemd/*.socket /etc/systemd/system/
sudo systemctl daemon-reload
```

Run the deploy script. This is what you'll re-run on every update too:

```bash
bash deployment/scripts/deploy.sh
```

`deploy.sh` does:

1. Creates `logs/`, `run/`, `static/media/`, `staticfiles/` if missing
2. Creates the venv at `.venv/` if missing
3. `pip install -r requirements.txt`
4. `python manage.py migrate --noinput`
5. `python manage.py collectstatic --noinput`
6. `python manage.py setup_groups` (idempotent)
7. Reloads systemd and starts `gunicorn.socket`, `gunicorn`,
   `celery-worker`, `celery-beat`

Verify all four services are active:

```bash
sudo systemctl status gunicorn celery-worker celery-beat
```

Create a Django superuser:

```bash
~/ecommerce/.venv/bin/python ~/ecommerce/manage.py createsuperuser
```

Quick local check that gunicorn is responding through its unix socket:

```bash
curl --unix-socket /run/gunicorn-ecommerce.sock http://localhost/healthz/
# {"status": "ok", "database": "ok"}
```

---

## Phase C — nginx + HTTPS

**Edit the nginx config** to substitute your real domain in place of `_`:

```bash
sudo cp deployment/nginx/ecommerce.conf /etc/nginx/sites-available/ecommerce
sudo sed -i 's/server_name _;/server_name yourdomain.com www.yourdomain.com;/' \
    /etc/nginx/sites-available/ecommerce
```

Activate the site and remove the default:

```bash
sudo ln -sf /etc/nginx/sites-available/ecommerce /etc/nginx/sites-enabled/ecommerce
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

At this point the site is reachable over **plain HTTP**. Confirm by
hitting `http://yourdomain.com/healthz/` from your laptop.

Now obtain an HTTPS certificate from Let's Encrypt. certbot will edit
the nginx config in place to add the 443 server block, redirect 80 → 443,
and install a renewal timer:

```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Confirm renewals will work:

```bash
sudo certbot renew --dry-run
```

---

## Phase D — Operations

### Updating the app

```bash
cd ~/ecommerce
git pull
bash deployment/scripts/deploy.sh
```

`deploy.sh` is idempotent — it'll re-install deps, run any new
migrations, recollect static, and `systemctl restart` the services.

### Logs

```bash
# gunicorn — request log + error log + journald
journalctl -u gunicorn -f
tail -f ~/ecommerce/logs/gunicorn-access.log
tail -f ~/ecommerce/logs/gunicorn-error.log

# celery
journalctl -u celery-worker -f
journalctl -u celery-beat -f
tail -f ~/ecommerce/logs/celery-worker1.log
tail -f ~/ecommerce/logs/celery-beat.log

# Django app log
tail -f ~/ecommerce/logs/django.log

# nginx
sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

### Backups

Add the daily backup to the deploy user's crontab:

```bash
crontab -e
```

Append:

```cron
30 3 * * *  /home/deploy/ecommerce/deployment/scripts/backup.sh
```

Backups land in `~/backups/`, gzipped, with a 30-day retention window
pruned automatically. Periodically copy them off-host (rclone to
DigitalOcean Spaces, scp to a workstation, etc.) — keeping backups on
the same Droplet is a single-failure pattern.

### Restoring from backup

```bash
# Drop and recreate the database (destructive — make sure you really
# want to roll back).
sudo systemctl stop gunicorn celery-worker celery-beat
sudo -u postgres psql -c "DROP DATABASE ecommerce;"
sudo -u postgres psql -c "CREATE DATABASE ecommerce OWNER ecommerce ENCODING 'UTF8';"

# Restore the dump.
gunzip -c ~/backups/db-2026-04-30-0330.sql.gz \
  | PGPASSWORD=STRONG_PG_PASSWORD psql -h localhost -U ecommerce ecommerce

# Restore media if needed.
tar -xzf ~/backups/media-2026-04-30-0330.tar.gz -C ~/ecommerce/

sudo systemctl start gunicorn celery-worker celery-beat
```

### Monitoring

Install the DigitalOcean monitoring agent so you get CPU / RAM / disk
graphs and alerting from the DO control panel:

```bash
curl -sSL https://repos.insights.digitalocean.com/install.sh | sudo bash
```

Optional: set up uptime monitoring (UptimeRobot, BetterStack, etc.) to
hit `https://yourdomain.com/healthz/` every minute and page on failure.

### Rotating secrets

When you rotate the Django secret, Postgres password, or Redis password:

1. Edit `/home/deploy/ecommerce/.env`
2. For Postgres: `sudo -u postgres psql -c "ALTER USER ecommerce WITH PASSWORD 'NEW_PW';"`
3. For Redis: edit `/etc/redis/redis.conf`, then `sudo systemctl restart redis-server`
4. `sudo systemctl restart gunicorn celery-worker celery-beat`

Existing user sessions remain valid after a Django secret rotation
because session cookies are signed, not encrypted with the secret —
but anything signed with the old secret (password reset tokens,
URL signatures) will become invalid.

---

## Troubleshooting

**`gunicorn` won't start, journalctl shows `ImproperlyConfigured: DATABASE_URL is required`**
— `.env` is missing or `DJANGO_ENV=production` isn't set. Check
`/home/deploy/ecommerce/.env` exists and is readable by the deploy user.

**`502 Bad Gateway` from nginx**
— gunicorn isn't listening on the socket. `sudo systemctl status gunicorn`
to find out why. Common causes: bad `.env`, migration failed, missing
dependency.

**`504 Gateway Timeout` on long-running views**
— bump `proxy_read_timeout` in
`/etc/nginx/sites-available/ecommerce` and `--timeout` in
`gunicorn.service`. Don't go above ~120s without good reason; the right
fix is usually moving the work into a celery task.

**Static files 404**
— `bash deployment/scripts/deploy.sh` re-runs collectstatic. If a CSS
file references an asset that wasn't shipped, whitenoise's strict
manifest mode will fail collectstatic; the fix is in
`config/settings/production.py` (`WHITENOISE_MANIFEST_STRICT = False`)
or shipping the missing asset.

**Postgres / Redis won't accept connections from the app**
— check the host in `DATABASE_URL` / `REDIS_URL` is `localhost`, not
`db` or `redis` (those are docker hostnames). Check Postgres is bound
to localhost (`pg_hba.conf`) and Redis has the right `requirepass` /
`bind` settings.

---

## What this guide does NOT cover

- **Multi-region or multi-Droplet deploys.** Single-Droplet only. If you
  need to scale, you'll outgrow this guide and want a managed Postgres,
  managed Redis, and a load balancer in front of multiple gunicorn
  Droplets.
- **CI/CD.** Deploys are manual via SSH + `git pull + deploy.sh`.
  Adding a GitHub Action that SSHs into the Droplet is straightforward
  if/when you want it.
- **Off-host backup storage.** `backup.sh` lands tarballs locally;
  shipping them to DO Spaces or another bucket is a separate cron line.
- **Container-orchestrator deployments.** The `deployment/docker/`
  folder has the artifacts for that path, but production using systemd
  is a different operating model from production using k8s — don't try
  to mix.
