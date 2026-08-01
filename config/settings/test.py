"""Settings used by automated tests and CI.

Keep normal operational audit/security logging out of test output: assertions
still exercise those paths, but test failures remain readable.
"""

from .development import *  # noqa: F401,F403


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'null': {'class': 'logging.NullHandler'},
    },
    'root': {'handlers': ['null'], 'level': 'CRITICAL'},
    'loggers': {
        'django.security': {'handlers': ['null'], 'level': 'CRITICAL', 'propagate': False},
        'auth': {'handlers': ['null'], 'level': 'CRITICAL', 'propagate': False},
        'audit': {'handlers': ['null'], 'level': 'CRITICAL', 'propagate': False},
    },
}
