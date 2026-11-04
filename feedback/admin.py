from django.contrib import admin

from .models import CustomerFeedback


@admin.register(CustomerFeedback)
class CustomerFeedbackAdmin(admin.ModelAdmin):
    list_display = ('rating', 'visit_type', 'name', 'status', 'created_at')
    list_filter = ('rating', 'visit_type', 'status', 'created_at')
    search_fields = ('name', 'contact', 'liked_most', 'improvement')
    readonly_fields = ('created_at',)

