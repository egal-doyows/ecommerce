from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import StaffCompensation, StaffBankDetails, PaymentRecord


@admin.register(StaffCompensation)
class StaffCompensationAdmin(ModelAdmin):
    list_display = ('user', 'compensation_type', 'commission_scope', 'commission_rate_regular', 'commission_rate_premium', 'salary_amount', 'payment_frequency', 'updated_at')
    list_filter = ('compensation_type', 'commission_scope', 'payment_frequency')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        (None, {
            'fields': ('user', 'compensation_type'),
        }),
        ('Commission Settings', {
            'fields': ('commission_scope', 'commission_rate_regular', 'commission_rate_premium'),
            'description': 'Only applies when compensation type is Commission.',
        }),
        ('Salary Settings', {
            'fields': ('salary_amount', 'payment_frequency'),
            'description': 'Only applies when compensation type is Salary.',
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(PaymentRecord)
class PaymentRecordAdmin(ModelAdmin):
    list_display = ('staff', 'amount', 'eligible_sales', 'total_sales', 'payment_type', 'period_start', 'period_end', 'status', 'disbursement_method', 'paid_at')
    list_filter = ('status', 'payment_type', 'disbursement_method')
    search_fields = ('staff__username',)
    readonly_fields = ('created_at',)
    actions = ['mark_as_paid']

    @admin.action(description='Mark selected payments as paid')
    def mark_as_paid(self, request, queryset):
        for record in queryset.filter(status='pending'):
            record.mark_paid()
        self.message_user(request, f'{queryset.count()} payment(s) marked as paid.')


@admin.register(StaffBankDetails)
class StaffBankDetailsAdmin(ModelAdmin):
    list_display = ('user', 'bank_name', 'account_name', 'account_number', 'branch')
    search_fields = ('user__username', 'bank_name', 'account_number')
