#!/bin/bash
# install.sh — one-shot Droplet provisioning.
# Run as root (or with sudo) on a fresh Ubuntu 22.04 box. Idempotent
# where possible, but assumes the host is otherwise unconfigured.

set -euo pipefail

echo "==> Updating system..."
apt update && apt upgrade -y

echo "==> Installing system dependencies..."
apt install -y \
    python3.12 python3.12-venv python3-pip \
    postgresql postgresql-contrib \
    redis-server \
    nginx \
    certbot python3-certbot-nginx \
    git curl ufw fail2ban unattended-upgrades \
    libpq-dev build-essential \
    libjpeg-dev zlib1g-dev \
    libpango-1.0-0 libpangoft2-1.0-0    # reportlab font support

echo "==> Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> Enabling unattended security updates..."
dpkg-reconfigure --priority=low unattended-upgrades

echo "==> Configuring fail2ban..."
systemctl enable --now fail2ban

echo "==> Setting up deploy user (if not present)..."
if ! id deploy &>/dev/null; then
    adduser --disabled-password --gecos "" deploy
    usermod -aG www-data deploy
    mkdir -p /home/deploy/.ssh
    if [ -f /root/.ssh/authorized_keys ]; then
        cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
        chown -R deploy:deploy /home/deploy/.ssh
        chmod 700 /home/deploy/.ssh
        chmod 600 /home/deploy/.ssh/authorized_keys
    fi
fi

echo "==> Configuring Postgres..."
systemctl enable --now postgresql
sudo -u postgres psql <<'EOF'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'ecommerce') THEN
        CREATE USER ecommerce WITH PASSWORD 'CHANGE_ME_DURING_INSTALL';
    END IF;
END
$$;

SELECT 'CREATE DATABASE ecommerce OWNER ecommerce ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ecommerce')
\gexec

GRANT ALL PRIVILEGES ON DATABASE ecommerce TO ecommerce;
EOF

echo "==> Configuring Redis (bind localhost, requirepass)..."
sed -i 's/^# requirepass .*/requirepass CHANGE_ME_DURING_INSTALL/' /etc/redis/redis.conf
sed -i 's/^bind .*/bind 127.0.0.1 ::1/' /etc/redis/redis.conf
systemctl restart redis-server
systemctl enable redis-server

cat <<'NEXT'

==> System setup complete. Next steps:

  1. Set a strong Postgres password:
       sudo -u postgres psql -c "ALTER USER ecommerce WITH PASSWORD 'STRONG_PW';"

  2. Set a strong Redis password:
       sudo sed -i 's/CHANGE_ME_DURING_INSTALL/STRONG_PW/' /etc/redis/redis.conf
       sudo systemctl restart redis-server

  3. Switch to the deploy user:
       su - deploy

  4. Clone the repo into /home/deploy/ecommerce, create .env, then run
     deployment/scripts/deploy.sh.

NEXT
