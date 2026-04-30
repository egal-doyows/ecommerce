from django.apps import AppConfig


class MenuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'menu'

    def ready(self):
        from core.file_cleanup import register_file_cleanup
        from .models import MenuItem, RestaurantSettings
        register_file_cleanup(MenuItem, fields=['image'])
        register_file_cleanup(RestaurantSettings, fields=['logo'])
