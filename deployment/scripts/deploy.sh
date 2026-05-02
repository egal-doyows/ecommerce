#!/bin/bash
# deploy.sh — deploy or update the app on the Droplet.
# Run as the `deploy` user from /home/deploy/ecommerce/. Safe to re-run.

set -euo pipefail

APP_DIR=/home/deploy/ecommerce
VENV_DIR=$APP_DIR/.venv

cd "$APP_DIR"

# manage.py reads os.environ directly (no dotenv loader). The systemd units
# pull env from .env via EnvironmentFile=, but bare `manage.py` invocations
# below don't, so source it here too. Required: DJANGO_SECRET_KEY, DATABASE_URL.
if [ -f "$APP_DIR/.env" ]; then
    set -a; . "$APP_DIR/.env"; set +a
else
    echo "ERROR: $APP_DIR/.env is missing — required for migrate/collectstatic." >&2
    exit 1
fi

echo "==> Ensuring runtime dirs exist..."
mkdir -p logs run media staticfiles

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating venv..."
    python3.12 -m venv "$VENV_DIR"
fi

echo "==> Installing/updating dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

echo "==> Running migrations..."
"$VENV_DIR/bin/python" manage.py migrate --noinput

echo "==> Collecting static files..."
"$VENV_DIR/bin/python" manage.py collectstatic --noinput

echo "==> Setting up groups (idempotent)..."
"$VENV_DIR/bin/python" manage.py setup_groups || \
    echo "(setup_groups not present or already run — continuing)"

echo "==> Reloading systemd and (re)starting services..."
sudo systemctl daemon-reload
if systemctl is-active --quiet gunicorn; then
    sudo systemctl restart gunicorn celery-worker celery-beat
else
    sudo systemctl enable --now gunicorn.socket gunicorn celery-worker celery-beat
fi

echo
echo "==> Done. Check status with:"
echo "    sudo systemctl status gunicorn celery-worker celery-beat"
echo "    journalctl -u gunicorn -f"
