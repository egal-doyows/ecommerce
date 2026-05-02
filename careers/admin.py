from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import JobOpening


@admin.register(JobOpening)
class JobOpeningAdmin(ModelAdmin):
    list_display = ('title', 'employment_type', 'location', 'is_open', 'posted_at')
    list_filter = ('is_open', 'employment_type')
    list_editable = ('is_open',)
    search_fields = ('title', 'summary', 'description')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('posted_at', 'updated_at')

    fieldsets = (
        ('The role', {
            'fields': ('title', 'slug', 'employment_type', 'location', 'is_open'),
        }),
        ('Description', {
            'fields': ('summary', 'description', 'requirements'),
        }),
        ('How to apply', {
            'description': (
                'Tell candidates exactly what to do — email an address, drop a CV at '
                'the counter, fill an external form, etc. This is shown verbatim on '
                'the public page.'
            ),
            'fields': ('how_to_apply',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('posted_at', 'updated_at'),
        }),
    )
