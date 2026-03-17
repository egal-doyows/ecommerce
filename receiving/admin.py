from django.contrib import admin
from .models import GoodsReceipt, GoodsReceiptItem


class GoodsReceiptItemInline(admin.TabularInline):
    model = GoodsReceiptItem
    extra = 0


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ['grn_number', 'purchase_order', 'received_by', 'received_date']
    list_filter = ['received_date']
    inlines = [GoodsReceiptItemInline]
