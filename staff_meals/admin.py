from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import StaffMealLog, StaffMealItem


class StaffMealItemInline(TabularInline):
    model = StaffMealItem
    extra = 0


@admin.register(StaffMealLog)
class StaffMealLogAdmin(ModelAdmin):
    list_display = ['meal_number', 'meal_type', 'date', 'logged_by', 'item_count']
    list_filter = ['meal_type', 'date']
    inlines = [StaffMealItemInline]
