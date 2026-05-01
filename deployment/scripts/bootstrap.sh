#!/usr/bin/env bash
# bootstrap.sh — one-shot, idempotent setup for a fresh Ubuntu Droplet.
#
# Usage (run as root, from inside the cloned repo — anywhere on disk):
#   sudo bash deployment/scripts/bootstrap.sh
#
# Re-runs are safe and will pull updates + restart services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"

DEPLOY_USER=deploy
APP_DIR=/home/$DEPLOY_USER/ecommerce
VENV_DIR=$APP_DIR/.venv
PYTHON_BIN=python3.12

[ "$EUID" -eq 0 ] || { echo "Run as root: sudo bash $0" >&2; exit 1; }

# ─── 1. OS detection + Python 3.12 source ────────────────────────────────────
. /etc/os-release
case "${VERSION_ID:-}" in
    22.04)
        echo "==> Ubuntu 22.04 detected — adding deadsnakes PPA for Python 3.12..."
        if [ ! -f /etc/apt/sources.list.d/deadsnakes.list ]; then
            curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF23C5A6CF475977595C89F51BA6932366A755776" \
                -o /etc/apt/trusted.gpg.d/deadsnakes.asc
            echo "deb https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu jammy main" \
                > /etc/apt/sources.list.d/deadsnakes.list
        fi
        ;;
    24.04|24.10|25.*)
        echo "==> Ubuntu $VERSION_ID — Python 3.12 in default repos."
        ;;
    *)
        echo "Unsupported Ubuntu version: ${VERSION_ID:-unknown}" >&2
        exit 1
        ;;
esac

export DEBIAN_FRONTEND=noninteractive

# ─── 2. System packages ──────────────────────────────────────────────────────
echo "==> apt update + upgrade..."
apt-get update -y
apt-get upgrade -y

echo "==> Installing system dependencies..."
apt-get install -y \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    postgresql postgresql-contrib \
    redis-server \
    nginx \
    certbot python3-certbot-nginx \
    git curl ufw fail2ban unattended-upgrades \
    libpq-dev build-essential \
    libjpeg-dev zlib1g-dev \
    libpango-1.0-0 libpangoft2-1.0-0

# ─── 3. Firewall ─────────────────────────────────────────────────────────────
echo "==> Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# ─── 4. fail2ban + unattended-upgrades ───────────────────────────────────────
systemctl enable --now fail2ban
echo 'unattended-upgrades unattended-upgrades/enable_auto_updates boolean true' \
    | debconf-set-selections
dpkg-reconfigure -f noninteractive unattended-upgrades

# ─── 5. Deploy user ──────────────────────────────────────────────────────────
if ! id "$DEPLOY_USER" &>/dev/null; then
    echo "==> Creating $DEPLOY_USER user..."
    adduser --disabled-password --gecos "" "$DEPLOY_USER"
    usermod -aG www-data "$DEPLOY_USER"
    if [ -f /root/.ssh/authorized_keys ]; then
        mkdir -p "/home/$DEPLOY_USER/.ssh"
        cp /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
        chown -R "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
        chmod 700 "/home/$DEPLOY_USER/.ssh"
        chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
    fi
fi

# Allow deploy user to manage app services without a password
cat > /etc/sudoers.d/deploy-services <<'EOF'
deploy ALL=(root) NOPASSWD: /bin/systemctl daemon-reload, /bin/systemctl restart gunicorn, /bin/systemctl restart celery-worker, /bin/systemctl restart celery-beat, /bin/systemctl enable --now gunicorn.socket gunicorn celery-worker celery-beat, /bin/systemctl reload nginx
EOF
chmod 440 /etc/sudoers.d/deploy-services

# ─── 6. Place repo at $APP_DIR ───────────────────────────────────────────────
if [ "$SOURCE_REPO" != "$APP_DIR" ]; then
    if [ -d "$APP_DIR/.git" ]; then
        echo "==> $APP_DIR already a git repo — pulling..."
        sudo -u "$DEPLOY_USER" git -C "$APP_DIR" pull --ff-only || true
    else
        echo "==> Copying repo to $APP_DIR..."
        mkdir -p "$APP_DIR"
        cp -a "$SOURCE_REPO/." "$APP_DIR/"
        chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
    fi
fi

# ─── 7. Generate .env (idempotent — never overwrites existing) ───────────────
if [ ! -f "$APP_DIR/.env" ]; then
    echo "==> Generating .env with random secrets..."
    SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')
    DB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    REDIS_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    cat > "$APP_DIR/.env" <<EOF
DJANGO_SECRET_KEY=$SECRET_KEY
DJANGO_DEBUG=False
DJANGO_ENV=production
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=
DATABASE_URL=postgres://ecommerce:$DB_PASSWORD@localhost:5432/ecommerce
POSTGRES_DB=ecommerce
POSTGRES_USER=ecommerce
POSTGRES_PASSWORD=$DB_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@localhost:6379/0
CELERY_BROKER_URL=redis://:$REDIS_PASSWORD@localhost:6379/0
CELERY_RESULT_BACKEND=redis://:$REDIS_PASSWORD@localhost:6379/0
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL="Restaurant <noreply@example.com>"
EOF
    chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
else
    echo "==> .env exists — leaving it untouched."
fi

# Source .env so we can use its passwords for Postgres / Redis config
set -a; . "$APP_DIR/.env"; set +a

# ─── 8. Postgres ─────────────────────────────────────────────────────────────
echo "==> Configuring Postgres..."
systemctl enable --now postgresql
sudo -u postgres psql -v ON_ERROR_STOP=1 <<EOF
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = '$POSTGRES_USER') THEN
        CREATE USER $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';
    ELSE
        ALTER USER $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';
    END IF;
END
\$\$;
SELECT 'CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$POSTGRES_DB')
\gexec
GRANT ALL PRIVILEGES ON DATABASE $POSTGRES_DB TO $POSTGRES_USER;
EOF

# ─── 9. Redis ────────────────────────────────────────────────────────────────
echo "==> Configuring Redis..."
REDIS_PW_LITERAL=$(printf '%s' "$REDIS_URL" | sed -E 's|redis://:([^@]*)@.*|\1|')
sed -i -E "s|^# *requirepass .*|requirepass $REDIS_PW_LITERAL|" /etc/redis/redis.conf
sed -i -E "s|^requirepass .*|requirepass $REDIS_PW_LITERAL|" /etc/redis/redis.conf
sed -i -E 's|^bind .*|bind 127.0.0.1 ::1|' /etc/redis/redis.conf
systemctl enable redis-server
systemctl restart redis-server

# ─── 10. App: venv, deps, migrations, static, groups ────────────────────────
echo "==> App setup as $DEPLOY_USER..."
sudo -u "$DEPLOY_USER" bash <<EOF
set -euo pipefail
cd "$APP_DIR"
mkdir -p logs run static/media staticfiles
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON_BIN -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt
"$VENV_DIR/bin/python" manage.py migrate --noinput
"$VENV_DIR/bin/python" manage.py collectstatic --noinput
"$VENV_DIR/bin/python" manage.py setup_groups || true
EOF

# ─── 11. systemd units ──────────────────────────────────────────────────────
echo "==> Installing systemd units..."
cp "$APP_DIR"/deployment/systemd/*.service /etc/systemd/system/
cp "$APP_DIR"/deployment/systemd/*.socket  /etc/systemd/system/
systemctl daemon-reload

# ─── 12. nginx site ─────────────────────────────────────────────────────────
echo "==> Installing nginx site..."
cp "$APP_DIR/deployment/nginx/ecommerce.conf" /etc/nginx/sites-available/ecommerce
ln -sf /etc/nginx/sites-available/ecommerce /etc/nginx/sites-enabled/ecommerce
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ─── 13. Start services ─────────────────────────────────────────────────────
echo "==> Enabling + starting app services..."
systemctl enable --now gunicorn.socket gunicorn celery-worker celery-beat
systemctl restart gunicorn celery-worker celery-beat

cat <<DONE

==> Bootstrap complete.

   Status:   sudo systemctl status gunicorn celery-worker celery-beat
   Logs:     sudo journalctl -u gunicorn -f
   Open:     http://<droplet-ip>/

Next steps:
  1. Edit $APP_DIR/.env — set DJANGO_ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, EMAIL_* :
       sudo -u $DEPLOY_USER nano $APP_DIR/.env
  2. Restart after editing:
       sudo systemctl restart gunicorn
  3. (Optional) Get HTTPS:
       sudo certbot --nginx -d yourdomain.com
DONE
