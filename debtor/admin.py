from django.contrib import admin
from .models import Debtor, DebtorTransaction, DebtorPaymentAllocation


@admin.register(Debtor)
class DebtorAdmin(admin.ModelAdmin):
    list_display = ['name', 'contact_person', 'phone', 'is_active']
    search_fields = ['name', 'contact_person']


@admin.register(DebtorTransaction)
class DebtorTransactionAdmin(admin.ModelAdmin):
    list_display = ['debtor', 'transaction_type', 'amount', 'amount_paid', 'description', 'date']
    list_filter = ['transaction_type', 'debtor']
    search_fields = ['description', 'reference']


@admin.register(DebtorPaymentAllocation)
class DebtorPaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ['payment', 'invoice', 'amount']
