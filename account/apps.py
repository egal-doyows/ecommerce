from django.apps import AppConfig


class AccountConfig(AppConfig):
    name = 'account'

    def ready(self):
        from core.file_cleanup import register_file_cleanup
        from .models import WaiterCode
        register_file_cleanup(WaiterCode, fields=['photo'])
