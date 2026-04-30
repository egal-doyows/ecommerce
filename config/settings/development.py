"""
Development settings — relaxed defaults, SQLite fallback allowed.
"""

import os

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, _parse_database_url


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
