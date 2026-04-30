from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'hr'
    verbose_name = 'Human Resources'

    def ready(self):
        import hr.approvals  # noqa: F401
        from core.file_cleanup import register_file_cleanup
        from .models import Document
        register_file_cleanup(Document, fields=['file'])
