from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import GoodsReceipt, GoodsReceiptItem


class GoodsReceiptItemInline(TabularInline):
    model = GoodsReceiptItem
    extra = 0


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(ModelAdmin):
    list_display = ['grn_number', 'purchase_order', 'received_by', 'received_date']
    list_filter = ['received_date']
    inlines = [GoodsReceiptItemInline]
