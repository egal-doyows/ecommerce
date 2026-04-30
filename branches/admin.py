from django.contrib import admin
from .models import Branch, UserBranch


class UserBranchInline(admin.TabularInline):
    model = UserBranch
    extra = 0


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'phone', 'is_active')
    prepopulated_fields = {'code': ('name',)}
    inlines = [UserBranchInline]


@admin.register(UserBranch)
class UserBranchAdmin(admin.ModelAdmin):
    list_display = ('user', 'branch', 'is_primary')
    list_filter = ('branch', 'is_primary')
