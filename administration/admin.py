from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Account, Transaction


@admin.register(Account)
class AccountAdmin(ModelAdmin):
    list_display = ('name', 'account_type', 'is_active', 'created_at')
    list_filter = ('account_type', 'is_active')


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    list_display = ('account', 'transaction_type', 'amount', 'description', 'reference_type', 'created_at', 'created_by')
    list_filter = ('transaction_type', 'account', 'reference_type')
    search_fields = ('description',)
    readonly_fields = ('created_at',)
