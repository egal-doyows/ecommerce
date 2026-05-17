"""
Admin views for ML output tables.

Focus is on the ModelRun health view — operators need a single-screen
green/red view to know whether nightly training succeeded.
"""

from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import (
    AnomalyEvent, BasketRule, DemandForecast, MenuClass, ModelRun,
    ReorderSuggestion, WeatherObservation,
)


@admin.register(ModelRun)
class ModelRunAdmin(ModelAdmin):
    list_display = (
        'model_name', 'status_badge', 'started_at', 'finished_at',
        'rows_used', 'rows_written', 'metric_summary',
    )
    list_filter = ('model_name', 'status')
    search_fields = ('model_name', 'error')
    readonly_fields = (
        'model_name', 'status', 'started_at', 'finished_at',
        'rows_used', 'rows_written',
        'metric_name', 'metric_value', 'baseline_value', 'error',
    )
    ordering = ('-started_at',)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colours = {
            'ok': '#16a34a',
            'failed': '#dc2626',
            'skipped': '#d97706',
            'running': '#6b7280',
        }
        c = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:8px;font-size:11px;">{}</span>',
            c, obj.get_status_display(),
        )

    @admin.display(description='Metric')
    def metric_summary(self, obj):
        if obj.metric_value is None:
            return '—'
        base = f' (baseline {obj.baseline_value:.3f})' if obj.baseline_value is not None else ''
        return f'{obj.metric_name} {obj.metric_value:.3f}{base}'


@admin.register(DemandForecast)
class DemandForecastAdmin(ModelAdmin):
    list_display = ('menu_item', 'date', 'hour', 'qty_p50', 'qty_p90', 'source', 'trained_at')
    list_filter = ('source', 'date')
    search_fields = ('menu_item__title',)
    readonly_fields = ('trained_at',)
    date_hierarchy = 'date'


@admin.register(ReorderSuggestion)
class ReorderSuggestionAdmin(ModelAdmin):
    list_display = (
        'inventory_item', 'suggested_qty', 'needed_by', 'days_of_cover',
        'status', 'source', 'computed_at',
    )
    list_filter = ('status', 'source')
    search_fields = ('inventory_item__name',)
    readonly_fields = ('computed_at',)


@admin.register(AnomalyEvent)
class AnomalyEventAdmin(ModelAdmin):
    list_display = (
        'occurred_on', 'subject_label', 'metric', 'observed_value',
        'expected_value', 'z_score', 'direction', 'dismissed',
    )
    list_filter = ('metric', 'direction', 'dismissed', 'subject_type')
    search_fields = ('subject_label',)
    readonly_fields = ('detected_at',)
    date_hierarchy = 'occurred_on'


@admin.register(BasketRule)
class BasketRuleAdmin(ModelAdmin):
    list_display = ('antecedent', 'consequent', 'support', 'confidence', 'lift', 'source', 'refreshed_at')
    list_filter = ('source',)
    search_fields = ('antecedent__title', 'consequent__title')
    readonly_fields = ('refreshed_at',)


@admin.register(WeatherObservation)
class WeatherObservationAdmin(ModelAdmin):
    list_display = (
        'date', 'source', 'temp_max_c', 'temp_min_c',
        'precipitation_mm', 'is_rainy', 'weather_code', 'fetched_at',
    )
    list_filter = ('source', 'is_rainy')
    date_hierarchy = 'date'
    readonly_fields = ('fetched_at',)


@admin.register(MenuClass)
class MenuClassAdmin(ModelAdmin):
    list_display = (
        'menu_item', 'classification', 'window_start', 'window_end',
        'units_sold', 'margin_pct', 'popularity_pct', 'source',
    )
    list_filter = ('classification', 'source')
    search_fields = ('menu_item__title',)
    readonly_fields = ('computed_at',)
