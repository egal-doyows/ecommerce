from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import WaiterCode


@admin.register(WaiterCode)
class WaiterCodeAdmin(ModelAdmin):
    list_display = ('user', 'code', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('user__username', 'code')
    readonly_fields = ('created_at',)

    def save_model(self, request, obj, form, change):
        if not obj.code:
            obj.code = WaiterCode.generate_code()
        super().save_model(request, obj, form, change)
