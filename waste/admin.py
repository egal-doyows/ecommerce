from django.contrib import admin
from .models import WasteLog, WasteItem


class WasteItemInline(admin.TabularInline):
    model = WasteItem
    extra = 0


@admin.register(WasteLog)
class WasteLogAdmin(admin.ModelAdmin):
    list_display = ['waste_number', 'reason', 'date', 'logged_by', 'item_count']
    list_filter = ['reason', 'date']
    inlines = [WasteItemInline]
