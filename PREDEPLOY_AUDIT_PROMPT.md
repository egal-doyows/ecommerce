# Pre-Deployment Infrastructure Audit

You are auditing a Django restaurant POS to determine if it's ready for production deployment to DigitalOcean. Do NOT fix anything. Do NOT make any changes. Your only job is to inspect and report.

Output your findings as a structured report I can paste back to my consultant.

## What to check

### 1. Docker setup

Check if these files exist at the project root:

- `Dockerfile`
- `docker-compose.yml` (or `docker-compose.yaml`)
- `.dockerignore`
- `entrypoint.sh` or similar startup script

For each file that exists, report:
- File path
- File size in lines
- A 5-line summary of what it does
- Any obvious issues (e.g., running as root, no healthcheck, exposing dev ports)

If `docker-compose.yml` exists, list every service defined and what each does (web, db, redis, celery, celery-beat, nginx, etc).

### 2. Settings & secrets

Look at `config/settings.py` (or `config/settings/`):

- Is it a single file, or split into `base.py` / `development.py` / `production.py`?
- What is the value of `DEBUG`? Is it hardcoded `True`, or read from env?
- What is `SECRET_KEY`? Hardcoded, or read from env?
- What is `ALLOWED_HOSTS`? Empty list, hardcoded, or env-driven?
- What database is configured? (SQLite path, or Postgres URL from env?)
- What email backend is configured? Are credentials hardcoded?
- Any other hardcoded credentials, API keys, or passwords?
- Is `CSRF_TRUSTED_ORIGINS` configured?
- Are security middleware settings (`SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, etc.) configured?

Just report what you find. Don't fix.

### 3. Environment configuration

- Does `.env.example` exist? If yes, list its keys (NOT values).
- Does `.env` exist in the repo? (If yes, this is a security incident — flag it loudly.)
- Is `.env` in `.gitignore`?
- Is `python-decouple`, `django-environ`, or similar in `requirements.txt`?

### 4. Dependencies

- Is there a `requirements.txt`? Is Django pinned to a version?
- Is there a `requirements/` folder split (base.txt / dev.txt / prod.txt)?
- Are any of these present: `gunicorn`, `psycopg`/`psycopg2`, `redis`, `celery`, `whitenoise`, `dj-database-url`?
- Is `reportlab` listed? (It's used in code; often missing.)

### 5. Static & media files

- Is `STATIC_ROOT` set in settings?
- Is `MEDIA_ROOT` set?
- Is `whitenoise` configured in `MIDDLEWARE`?
- Are static files committed to the repo? (Check size of `static/` folder.)

### 6. Background tasks

- Is Celery configured? (Look for `celery.py` in the config folder.)
- Are there `tasks.py` files in any app?
- Is there scheduled work that needs Celery beat?

### 7. Database state

- Is `db.sqlite3` committed to the repo? (It shouldn't be.)
- How many migrations does each app have? (Just count files in each `migrations/` folder.)
- Are migrations consistent? (Run `python manage.py makemigrations --check --dry-run` and report output.)

### 8. Critical security issues already known

The previous audit identified these — confirm whether they've been fixed:

- C1: Hardcoded SECRET_KEY in settings.py
- C1: Hardcoded Gmail app password in settings.py
- C2: SQLite as production database
- C4: Both `purchasing.po_receive` AND `receiving.receipt_create` views exist (causes double-invoicing)
- C5: `place_order` swallows stock-deduction exceptions
- C6: No rate limiting on `waiter_login` (django-axes installed?)

For each, report: FIXED / NOT FIXED / PARTIALLY FIXED, with a one-line justification.

### 9. Production-readiness checklist

Run `python manage.py check --deploy` (if you can run Python) and paste the output verbatim. Don't try to fix what it complains about — just report.

## Output format

Produce the report as one Markdown document with this structure:

```
# Pre-Deployment Audit Report

## 1. Docker Setup
[findings]

## 2. Settings & Secrets
[findings]

## 3. Environment Configuration
[findings]

## 4. Dependencies
[findings]

## 5. Static & Media Files
[findings]

## 6. Background Tasks
[findings]

## 7. Database State
[findings]

## 8. Known Critical Issues — Status
- C1 SECRET_KEY: [status]
- C1 Email password: [status]
- C2 Production DB: [status]
- C4 Double-invoice: [status]
- C5 Silent stock failure: [status]
- C6 Auth rate limiting: [status]

## 9. `manage.py check --deploy` output
[paste verbatim]

## Summary
- Ready to deploy as-is? [yes / no / with caveats]
- Estimated work needed before production: [hours]
- Top 3 blockers, in order: [list]
```

Stop after the report. Do not start fixing.
