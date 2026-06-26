"""
Base settings shared by all environments.

Override-only settings (DEBUG defaults, security headers, DATABASE_URL handling)
live in development.py and production.py.
"""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ── Core ──────────────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    raise ValueError(
        'DJANGO_SECRET_KEY environment variable is required. '
        'Generate one with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"'
    )

DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')]


# ── Apps ──────────────────────────────────────────────────────────────────

INSTALLED_APPS = [
    # django-unfold must be listed BEFORE django.contrib.admin
    # so its template overrides are picked up.
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.sitemaps',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'auditlog',
    'mathfilters',
    'django.contrib.humanize',
    'crispy_forms',
    'django_celery_beat',
    # Local apps
    'public_site',
    'menu',
    'cart',
    'account',
    'staff_compensation',
    'administration',
    'supplier',
    'debtor',
    'purchasing',
    'receiving',
    'waste',
    'staff_meals',
    'expenses',
    'hr',
    'reports',
    'careers',
    'ml',
]

CRISPY_TEMPLATE_PACK = 'bootstrap4'


# ── Django admin theme (django-unfold) ────────────────────────────────────
# Brand palette derived from the Bean & Bite primary mark:
#   amber  E08E2A  (logo plinth)
#   red    B83E1E  (wordmark — used as `primary`)
#   espresso 2B1810
# Unfold colours are space-separated RGB triplets (0–255).

UNFOLD = {
    'SITE_TITLE': 'Bean & Bite Admin',
    'SITE_HEADER': 'Bean & Bite',
    'SITE_SUBHEADER': 'Restaurant management',
    'SITE_URL': '/',
    'SITE_SYMBOL': 'restaurant',  # Material Symbols icon name
    'SHOW_HISTORY': True,
    'SHOW_VIEW_ON_SITE': True,
    'THEME': None,                 # respect user's OS light/dark preference
    'LOGIN': {
        'image': lambda r: '/static/public_site/img/logo-on-red.png',
    },
    'COLORS': {
        # Brand brick-red ramp (primary)
        'primary': {
            '50':  '251 244 232',   # cream
            '100': '244 224 213',
            '200': '233 192 171',
            '300': '221 156 124',
            '400': '209 120 84',
            '500': '184 62 30',     # ← brand red #B83E1E
            '600': '156 51 24',
            '700': '142 44 18',     # red-deep
            '800': '107 33 14',
            '900': '74 24 9',
            '950': '42 26 16',      # espresso
        },
        'font': {
            'subtle-light': '107 82 64',
            'subtle-dark':  '156 138 122',
            'default-light': '42 26 16',
            'default-dark':  '251 244 232',
            'important-light': '42 26 16',
            'important-dark':  '255 255 255',
        },
    },
    'SIDEBAR': {
        'show_search': True,
        'show_all_applications': True,
        'navigation': [
            {
                'title': 'Public site',
                'separator': True,
                'items': [
                    {'title': 'Restaurant settings', 'icon': 'storefront',
                     'link': lambda r: '/restpos/admin/menu/restaurantsettings/'},
                    {'title': 'Menu items',          'icon': 'restaurant_menu',
                     'link': lambda r: '/restpos/admin/menu/menuitem/'},
                    {'title': 'Categories',          'icon': 'category',
                     'link': lambda r: '/restpos/admin/menu/category/'},
                    {'title': 'Accompaniment groups', 'icon': 'rule',
                     'link': lambda r: '/restpos/admin/menu/accompanimentgroup/'},
                    {'title': 'Accompaniment options', 'icon': 'tune',
                     'link': lambda r: '/restpos/admin/menu/accompanimentoption/'},
                    {'title': 'Job openings',        'icon': 'work',
                     'link': lambda r: '/restpos/admin/careers/jobopening/'},
                ],
            },
            {
                'title': 'Operations',
                'separator': True,
                'items': [
                    {'title': 'Orders',     'icon': 'receipt_long',
                     'link': lambda r: '/restpos/admin/menu/order/'},
                    {'title': 'Tables',     'icon': 'table_restaurant',
                     'link': lambda r: '/restpos/admin/menu/table/'},
                    {'title': 'Shifts',     'icon': 'schedule',
                     'link': lambda r: '/restpos/admin/menu/shift/'},
                    {'title': 'Inventory',  'icon': 'inventory_2',
                     'link': lambda r: '/restpos/admin/menu/inventoryitem/'},
                ],
            },
            {
                'title': 'People',
                'separator': True,
                'items': [
                    {'title': 'Users',  'icon': 'person',
                     'link': lambda r: '/restpos/admin/auth/user/'},
                    {'title': 'Groups', 'icon': 'groups',
                     'link': lambda r: '/restpos/admin/auth/group/'},
                ],
            },
        ],
    },
}


# ── Middleware ────────────────────────────────────────────────────────────

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'auditlog.middleware.AuditlogMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'menu.views.categories',
                'menu.views.restaurant_settings',
                'cart.context_processors.cart',
                'administration.context_processors.admin_role',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ── Password validation ───────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ── i18n / tz ─────────────────────────────────────────────────────────────

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ── Static & media ────────────────────────────────────────────────────────

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static/']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
# Keep user uploads OUT of STATICFILES_DIRS so collectstatic doesn't ship
# them, and so the served path is not also reachable under /static/media/.
MEDIA_ROOT = BASE_DIR / 'media'


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ── POS thermal receipt printing (QZ Tray) ────────────────────────────────
# Each Windows register runs QZ Tray and exposes the thermal printer as a
# Windows print queue. POS_RECEIPT_PRINTER is the *default* queue name the
# POS page prints to; a register may override it per-device from the POS UI.
POS_RECEIPT_PRINTER = os.environ.get('POS_RECEIPT_PRINTER', 'POS-80')
# Characters per line for the receipt (48 for an 80 mm POS-80 at Font A).
POS_RECEIPT_WIDTH = int(os.environ.get('POS_RECEIPT_WIDTH', '48'))

# QZ Tray request signing — lets cashiers print silently (no per-print
# security popup). Point these at the cert/key generated for the rollout
# (see deployment/qz/README.md). When the files are absent the POS still
# works; QZ just falls back to its unsigned mode (one allow-prompt per print).
QZ_CERT_PATH = os.environ.get(
    'QZ_CERT_PATH', str(BASE_DIR / 'deployment' / 'qz' / 'digital-certificate.txt'),
)
QZ_PRIVATE_KEY_PATH = os.environ.get(
    'QZ_PRIVATE_KEY_PATH', str(BASE_DIR / 'deployment' / 'qz' / 'private-key.pem'),
)


# ── Email ─────────────────────────────────────────────────────────────────

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'noreply@example.com')


# ── Cookie & header hardening (always-on) ─────────────────────────────────

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True


# ── Logging ───────────────────────────────────────────────────────────────

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs' / 'django.log',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django.security': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': True,
        },
        'auth': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'audit': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

(BASE_DIR / 'logs').mkdir(exist_ok=True)


# ── Celery ────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

try:
    from celery.schedules import crontab
    CELERY_BEAT_SCHEDULE = {
        'generate-monthly-payments': {
            'task': 'staff_compensation.tasks.generate_monthly_payment_records',
            'schedule': crontab(hour=2, minute=0, day_of_month=1),
        },
        'low-stock-alerts': {
            'task': 'staff_compensation.tasks.send_low_stock_alerts',
            'schedule': crontab(hour=8, minute=0),
        },
        # ── ML retraining (overnight, after the day's orders are settled) ──
        'ml-fetch-weather': {
            # Runs first so the forecast trainer has fresh weather to join on.
            'task': 'ml.fetch_weather',
            'schedule': crontab(hour=1, minute=30),
        },
        'ml-train-forecast': {
            'task': 'ml.train_forecast',
            'schedule': crontab(hour=2, minute=0),
        },
        'ml-compute-reorder': {
            'task': 'ml.compute_reorder',
            'schedule': crontab(hour=2, minute=30),
        },
        'ml-train-anomaly': {
            'task': 'ml.train_anomaly',
            'schedule': crontab(hour=3, minute=0),
        },
        'ml-compute-menu-class': {
            # Weekly — Monday early morning.
            'task': 'ml.compute_menu_class',
            'schedule': crontab(hour=4, minute=0, day_of_week=1),
        },
        'ml-train-basket': {
            # Weekly — Sunday late so Monday's POS has fresh upsells.
            'task': 'ml.train_basket',
            'schedule': crontab(hour=3, minute=30, day_of_week=0),
        },
        'ml-cleanup-model-runs': {
            'task': 'ml.cleanup_model_runs',
            'schedule': crontab(hour=4, minute=30),
        },
        'ml-daily-digest': {
            # Fires after all trainers complete (latest is basket at 03:30 Sun),
            # before the typical 8am open.
            'task': 'ml.daily_digest',
            'schedule': crontab(hour=7, minute=0),
        },
    }
except ImportError:
    pass


# ── Error monitoring (Sentry) ─────────────────────────────────────────────
# Opt-in. When SENTRY_DSN is set we initialise sentry-sdk with the Django
# and Celery integrations; otherwise this is a no-op so local dev needs
# nothing. Keep the imports inside the guard so the dep is optional too.

SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
SENTRY_ENVIRONMENT = os.environ.get('SENTRY_ENVIRONMENT', 'production' if not DEBUG else 'development')
SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.05'))
SENTRY_RELEASE = os.environ.get('SENTRY_RELEASE', '')

if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            release=SENTRY_RELEASE or None,
            integrations=[DjangoIntegration(), CeleryIntegration()],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,   # don't ship usernames/emails by default
        )
    except ImportError:
        # sentry-sdk not installed — silently skip rather than break startup.
        pass


# ── DRF ───────────────────────────────────────────────────────────────────

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}


# ── Audit log ─────────────────────────────────────────────────────────────

AUDITLOG_INCLUDE_ALL_MODELS = True

# ML output tables are written by nightly trainers in batches of hundreds of
# rows per run — auditing them would balloon the auditlog without value.
AUDITLOG_EXCLUDE_TRACKING_MODELS = (
    'ml.DemandForecast',
    'ml.ReorderSuggestion',
    'ml.AnomalyEvent',
    'ml.BasketRule',
    'ml.MenuClass',
    'ml.ModelRun',
    'ml.WeatherObservation',
)


# ── Rate limiting & cache ─────────────────────────────────────────────────

RATELIMIT_USE_CACHE = 'default'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

REDIS_URL = os.environ.get('REDIS_URL')
if REDIS_URL:
    CACHES['default'] = {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    }


# ── Database ──────────────────────────────────────────────────────────────
# Each environment defines its own DATABASES — see development.py / production.py.

def _parse_database_url(url):
    """Parse postgres://user:pass@host:port/dbname into a Django DATABASES entry."""
    import re
    match = re.match(
        r'postgres(?:ql)?://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<name>.+)',
        url,
    )
    if not match:
        return None
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': match.group('name'),
        'USER': match.group('user'),
        'PASSWORD': match.group('password'),
        'HOST': match.group('host'),
        'PORT': match.group('port'),
        'CONN_MAX_AGE': 600,
        'OPTIONS': {'connect_timeout': 10},
    }
