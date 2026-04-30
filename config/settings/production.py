"""
Production settings — strict, HTTPS-only, no silent fallbacks.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import _parse_database_url


# ── Database ──────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ImproperlyConfigured(
        'DATABASE_URL is required in production. Refusing to fall back to SQLite.'
    )

_parsed_db = _parse_database_url(DATABASE_URL)
if not _parsed_db:
    raise ImproperlyConfigured(
        'DATABASE_URL is malformed. Expected postgres://user:pass@host:port/dbname.'
    )
DATABASES = {'default': _parsed_db}


# ── HTTPS / cookie hardening ──────────────────────────────────────────────

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True


# ── Static files (whitenoise compressed manifest) ─────────────────────────

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}
# Vendored CSS references some sourcemap files that aren't shipped — don't
# fail the whole collectstatic over it.
WHITENOISE_MANIFEST_STRICT = False
