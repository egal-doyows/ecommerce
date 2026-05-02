from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Supplier, SupplierTransaction, SupplierPaymentAllocation


@admin.register(Supplier)
class SupplierAdmin(ModelAdmin):
    list_display = ['name', 'contact_person', 'phone', 'is_active']
    search_fields = ['name', 'contact_person']


@admin.register(SupplierTransaction)
class SupplierTransactionAdmin(ModelAdmin):
    list_display = ['supplier', 'transaction_type', 'amount', 'amount_paid', 'description', 'date']
    list_filter = ['transaction_type', 'supplier']
    search_fields = ['description', 'reference']


@admin.register(SupplierPaymentAllocation)
class SupplierPaymentAllocationAdmin(ModelAdmin):
    list_display = ['payment', 'invoice', 'amount']
    raw_id_fields = ['payment', 'invoice']
