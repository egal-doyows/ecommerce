from django.contrib import admin
from .models import TaxConfiguration


@admin.register(TaxConfiguration)
class TaxConfigurationAdmin(admin.ModelAdmin):
    list_display = ['tax_name', 'tax_rate', 'tax_type', 'is_enabled']

    def has_add_permission(self, request):
        return not TaxConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
