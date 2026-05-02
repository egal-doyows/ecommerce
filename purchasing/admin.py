from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import PurchaseOrder, PurchaseOrderItem


class PurchaseOrderItemInline(TabularInline):
    model = PurchaseOrderItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ModelAdmin):
    list_display = ['po_number', 'supplier', 'status', 'order_date', 'total', 'created_by']
    list_filter = ['status', 'supplier']
    search_fields = ['supplier__name', 'notes']
    inlines = [PurchaseOrderItemInline]


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(ModelAdmin):
    list_display = ['purchase_order', 'inventory_item', 'quantity', 'unit_price', 'received_quantity']
    list_filter = ['purchase_order__status']
