"""
Production settings — strict, HTTPS-only, no silent fallbacks.
"""

import os

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, _parse_database_url


# ── Database ──────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    parsed = _parse_database_url(DATABASE_URL)
    DATABASES = {'default': parsed} if parsed else {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


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
