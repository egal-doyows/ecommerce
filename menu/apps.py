from django.apps import AppConfig


class MenuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'menu'

    def ready(self):
        # Register cache-invalidation signal handlers.
        from . import signals  # noqa: F401

        # Audit the till-count + close fields on Shift. Every change writes
        # an immutable LogEntry (action, actor, old/new values, timestamp,
        # IP) via the auditlog middleware, surfaced on /reports/audit-trail/.
        from auditlog.registry import auditlog
        from .models import Shift
        auditlog.register(
            Shift,
            include_fields=[
                'counted_cash', 'counted_by', 'counted_at',
                'pending_close_at', 'ended_at', 'is_active',
                'starting_cash',
            ],
        )
