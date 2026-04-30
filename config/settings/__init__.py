"""
Settings dispatcher.

Selects which settings module to expose based on the DJANGO_ENV env var.

  DJANGO_ENV=production  → production.py
  DJANGO_ENV=development → development.py (default)

Callers may also set DJANGO_SETTINGS_MODULE=config.settings.production directly
to bypass this dispatcher.
"""

import os

_env = os.environ.get('DJANGO_ENV', 'development').lower()

if _env == 'production':
    from .production import *  # noqa: F401,F403
else:
    from .development import *  # noqa: F401,F403
