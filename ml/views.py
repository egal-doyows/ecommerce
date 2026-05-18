"""
Report views for ML outputs.

Each view reads from the DB tables that the nightly trainers write to.
A 'source=baseline' indicator flips a banner on so users know the data is
cold-start.
"""

from datetime import datetime, timedelta

from django.contrib import messages
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from menu.cache import get_restaurant_settings
from ml.models import (
    AnomalyEvent, BasketRule, DemandForecast, MenuClass, ModelRun,
    ReorderSuggestion,
)
from reports.utils import manager_required, supervisor_or_manager_required


def _settings_ctx():
    s = get_restaurant_settings()
    return {'currency_symbol': s.currency_symbol}


def _model_status(model_name):
    """Latest ModelRun summary for banner display."""
    return ModelRun.objects.filter(model_name=model_name).first()


# ── Index ────────────────────────────────────────────────────────────────

@manager_required
def ml_index(request):
    statuses = {
        name: _model_status(name)
        for name in ['forecast', 'reorder', 'anomaly', 'basket', 'menu_class']
    }
    return render(request, 'ml/index.html', {'statuses': statuses})


# ── Prep List (from DemandForecast) ──────────────────────────────────────

@supervisor_or_manager_required
def prep_list(request):
    today = timezone.localdate()
    target_str = request.GET.get('date')
    try:
        target = (
            datetime.strptime(target_str, '%Y-%m-%d').date()
            if target_str else today + timedelta(days=1)
        )
    except ValueError:
        target = today + timedelta(days=1)

    rows = (
        DemandForecast.objects
        .filter(date=target, hour__isnull=True)
        .select_related('menu_item__category')
        .order_by('-qty_p50')
    )
    is_baseline = any(r.source == 'baseline' for r in rows)
    run = _model_status('forecast')

    ctx = {
        'rows': rows,
        'target_date': target,
        'today': today,
        'tomorrow': today + timedelta(days=1),
        'is_baseline': is_baseline,
        'run': run,
        **_settings_ctx(),
    }
    return render(request, 'ml/prep_list.html', ctx)


# ── Suggested Reorders ───────────────────────────────────────────────────

@supervisor_or_manager_required
def reorders(request):
    show_resolved = request.GET.get('show') == 'all'
    qs = (
        ReorderSuggestion.objects
        .select_related('inventory_item__preferred_supplier')
    )
    if not show_resolved:
        qs = qs.filter(status='open')
    rows = list(qs.order_by('needed_by', 'inventory_item__name'))
    is_baseline = any(r.source == 'baseline' for r in rows)
    run = _model_status('reorder')

    return render(request, 'ml/reorders.html', {
        'rows': rows,
        'show_resolved': show_resolved,
        'is_baseline': is_baseline,
        'run': run,
        **_settings_ctx(),
    })


@manager_required
@require_POST
def dismiss_reorder(request, pk):
    sug = get_object_or_404(ReorderSuggestion, pk=pk)
    new_status = request.POST.get('status', 'dismissed')
    if new_status not in {'dismissed', 'ordered'}:
        new_status = 'dismissed'
    sug.status = new_status
    sug.resolved_at = timezone.now()
    sug.resolved_by = request.user
    sug.save()
    messages.success(request, f'Reorder for {sug.inventory_item.name} marked {new_status}.')
    return redirect('ml-reorders')


# ── Shift Exceptions (anomalies) ─────────────────────────────────────────

@manager_required
def exceptions(request):
    show_dismissed = request.GET.get('show') == 'all'
    qs = AnomalyEvent.objects.select_related('shift__waiter')
    if not show_dismissed:
        qs = qs.filter(dismissed=False)
    rows = list(qs.order_by('-occurred_on', '-z_score'))
    run = _model_status('anomaly')

    return render(request, 'ml/exceptions.html', {
        'rows': rows,
        'show_dismissed': show_dismissed,
        'run': run,
        **_settings_ctx(),
    })


@manager_required
@require_POST
def dismiss_exception(request, pk):
    ev = get_object_or_404(AnomalyEvent, pk=pk)
    ev.dismissed = True
    ev.dismissed_at = timezone.now()
    ev.dismissed_by = request.user
    ev.dismissal_reason = request.POST.get('reason', '')[:1000]
    ev.save()
    messages.success(request, 'Exception dismissed.')
    return redirect('ml-exceptions')


# ── Upsell pairs ─────────────────────────────────────────────────────────

@supervisor_or_manager_required
def upsell(request):
    rows = (
        BasketRule.objects
        .select_related('antecedent', 'consequent')
        .order_by('-lift', '-confidence')[:200]
    )
    is_baseline = any(r.source == 'baseline' for r in rows)
    run = _model_status('basket')

    return render(request, 'ml/upsell.html', {
        'rows': rows,
        'is_baseline': is_baseline,
        'run': run,
        **_settings_ctx(),
    })


# ── Menu engineering ─────────────────────────────────────────────────────

@manager_required
def menu_engineering(request):
    latest = MenuClass.objects.aggregate(end=Max('window_end'))['end']
    rows = (
        MenuClass.objects
        .filter(window_end=latest)
        .select_related('menu_item__category')
        .order_by('classification', '-revenue')
        if latest else []
    )
    by_class = {'star': [], 'plowhorse': [], 'puzzle': [], 'dog': []}
    for r in rows:
        by_class[r.classification].append(r)
    is_baseline = any(r.source == 'baseline' for r in rows) if rows else False
    run = _model_status('menu_class')

    return render(request, 'ml/menu_engineering.html', {
        'by_class': by_class,
        'rows': rows,
        'window_end': latest,
        'window_start': rows[0].window_start if rows else None,
        'is_baseline': is_baseline,
        'run': run,
        **_settings_ctx(),
    })
