"""
Report views for ML outputs.

Each view reads from the DB tables that the nightly trainers write to.
A 'source=baseline' indicator flips a banner on so users know the data is
cold-start.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import mean

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


@supervisor_or_manager_required
def insights_for_supervisors(request):
    """Landing page hub for the three ML reports supervisors can access.

    Keeps the supervisor sidebar tidy — one 'Insights' entry that fans
    out into Reorders, Prep List, and Upsell Pairs. Managers see the
    full /ml/ dashboard via the separate ml-index route."""
    return render(request, 'ml/supervisor_insights.html')


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


# ── Demand by Day of Week (from DemandForecast) ──────────────────────────

_WEEKDAY_ABBR = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
_WEEKDAY_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                 'Friday', 'Saturday', 'Sunday']


@supervisor_or_manager_required
def forecast_by_weekday(request):
    """Forward-looking demand broken down by day of week.

    Reads the 14-day forward DemandForecast window and answers two questions:
      1. Which upcoming days / weekdays are busiest and slowest? (staffing)
      2. Which weekday does each menu item peak on? (prep & purchasing)

    Pure aggregation over forecasts the nightly trainer already wrote — no
    extra storage, always fresh after a training run.
    """
    today = timezone.localdate()
    rows = (
        DemandForecast.objects
        .filter(date__gt=today, hour__isnull=True)
        .select_related('menu_item')
    )

    is_baseline = False
    by_date = defaultdict(lambda: {'qty': 0.0, 'revenue': Decimal('0')})
    item_profile = defaultdict(lambda: [0.0] * 7)   # item_id → per-weekday qty
    item_total = defaultdict(float)
    item_meta = {}

    for r in rows:
        if r.source == 'baseline':
            is_baseline = True
        by_date[r.date]['qty'] += r.qty_p50
        price = r.menu_item.price or Decimal('0')
        by_date[r.date]['revenue'] += price * Decimal(str(r.qty_p50))
        item_profile[r.menu_item_id][r.date.weekday()] += r.qty_p50
        item_total[r.menu_item_id] += r.qty_p50
        item_meta[r.menu_item_id] = r.menu_item

    # Upcoming days, in date order.
    upcoming = [
        {'date': d, 'weekday': _WEEKDAY_ABBR[d.weekday()],
         'qty': v['qty'], 'revenue': v['revenue']}
        for d, v in sorted(by_date.items())
    ]
    busiest = max(upcoming, key=lambda x: x['qty']) if upcoming else None
    slowest = min(upcoming, key=lambda x: x['qty']) if upcoming else None

    # Weekday summary: average predicted volume per weekday across the horizon.
    wd_vals = defaultdict(list)
    for u in sorted(by_date.items()):
        wd_vals[u[0].weekday()].append(u[1]['qty'])
    weekday_ranked = sorted(
        (
            {'name': _WEEKDAY_FULL[wd],
             'avg_qty': mean(wd_vals[wd]) if wd_vals.get(wd) else 0.0}
            for wd in range(7)
        ),
        key=lambda x: x['avg_qty'], reverse=True,
    )

    # Per-item peak weekday, items ordered by total predicted volume.
    items = []
    for item_id, profile in item_profile.items():
        if item_total[item_id] <= 0:
            continue
        peak_wd = max(range(7), key=lambda w: profile[w])
        items.append({
            'item': item_meta[item_id],
            'total': item_total[item_id],
            'profile': [round(v, 1) for v in profile],
            'peak_name': _WEEKDAY_FULL[peak_wd],
        })
    items.sort(key=lambda x: x['total'], reverse=True)

    ctx = {
        'upcoming': upcoming,
        'busiest': busiest,
        'slowest': slowest,
        'weekday_ranked': weekday_ranked,
        'weekday_abbr': _WEEKDAY_ABBR,
        'items': items,
        'today': today,
        'is_baseline': is_baseline,
        'run': _model_status('forecast'),
        **_settings_ctx(),
    }
    return render(request, 'ml/weekday_forecast.html', ctx)


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
