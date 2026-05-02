from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import WasteLog, WasteItem


class WasteItemInline(TabularInline):
    model = WasteItem
    extra = 0


@admin.register(WasteLog)
class WasteLogAdmin(ModelAdmin):
    list_display = ['waste_number', 'reason', 'date', 'logged_by', 'item_count']
    list_filter = ['reason', 'date']
    inlines = [WasteItemInline]
