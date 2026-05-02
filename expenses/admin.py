from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import ExpenseCategory, Expense


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(ModelAdmin):
    list_display = ['name', 'is_active']
    list_filter = ['is_active']


@admin.register(Expense)
class ExpenseAdmin(ModelAdmin):
    list_display = ['expense_number', 'category', 'description', 'amount', 'date',
                    'payment_method', 'recorded_by']
    list_filter = ['category', 'payment_method', 'date']
    search_fields = ['description', 'vendor', 'receipt_number']
