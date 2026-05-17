from django.apps import AppConfig


class MenuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'menu'

    def ready(self):
        # Register cache-invalidation signal handlers.
        from . import signals  # noqa: F401
