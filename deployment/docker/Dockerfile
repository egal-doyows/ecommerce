# Optional Docker image — production deploys use systemd + nginx.
# Build from the repo root:
#   docker build -f deployment/docker/Dockerfile -t ecommerce .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Drop privileges. Create runtime dirs first so they exist on volume init
# and inherit django ownership when compose mounts named volumes over them.
RUN groupadd --system django \
    && useradd --system --gid django --home-dir /app --shell /sbin/nologin django \
    && mkdir -p /app/logs /app/static/media /app/staticfiles \
    && chown -R django:django /app
USER django

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
