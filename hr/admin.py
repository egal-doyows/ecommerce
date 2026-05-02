from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    Department, Position, Employee, EmergencyContact,
    LeaveType, LeaveRequest, Document,
)


@admin.register(Department)
class DepartmentAdmin(ModelAdmin):
    list_display = ('name', 'is_active')


@admin.register(Position)
class PositionAdmin(ModelAdmin):
    list_display = ('title', 'department', 'is_active')


class EmergencyContactInline(TabularInline):
    model = EmergencyContact
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(ModelAdmin):
    list_display = ('employee_id', 'user', 'department', 'position', 'status')
    list_filter = ('status', 'department', 'employment_type')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'employee_id')
    inlines = [EmergencyContactInline]


@admin.register(LeaveType)
class LeaveTypeAdmin(ModelAdmin):
    list_display = ('name', 'days_allowed', 'is_paid', 'is_active')


@admin.register(LeaveRequest)
class LeaveRequestAdmin(ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'leave_type')


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = ('title', 'employee', 'category', 'uploaded_at')
