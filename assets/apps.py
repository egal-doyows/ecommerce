from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assets'

    def ready(self):
        from core.file_cleanup import register_file_cleanup
        from .models import Asset
        register_file_cleanup(Asset, fields=['image'])
