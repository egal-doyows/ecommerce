"""
Baseline strategies for cold-start (and the safety net when ML drifts).

Each baseline returns the same shape its ML counterpart writes to the DB,
so the report UI is agnostic to whether the source is 'ml' or 'baseline'.

Rule of thumb: a baseline should be the simplest thing that's not embarrassing.
"7-day moving average" is the bar — beat it or ship the moving average.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import mean

from django.db.models import Count, F, Sum
from django.utils import timezone

from menu.models import InventoryItem, MenuItem, Order, OrderItem, Recipe


# ── Forecast ──────────────────────────────────────────────────────────────

FORECAST_LOOKBACK_DAYS = 56  # ~8 weeks → ≥8 samples per weekday


def forecast_baseline(horizon_days=14):
    """
    Per item: predict each future day = mean of that *weekday's* daily sales
    over the last 8 weeks (missing days count as 0). Returns list of
    {menu_item_id, date, qty_p50, qty_p90} dicts.

    Weekday-aware so the cold-start/safety-net baseline still reflects
    'Saturdays sell more than Mondays'. p90 = p50 * 1.4 as a no-data guess
    at variance.
    """
    today = timezone.localdate()
    lookback_start = today - timedelta(days=FORECAST_LOOKBACK_DAYS)

    rows = (
        OrderItem.objects
        .filter(order__status='paid', order__created_at__date__gte=lookback_start)
        .values('menu_item_id', 'order__created_at__date')
        .annotate(qty=Sum('quantity'))
    )
    by_item_by_day = defaultdict(dict)
    for r in rows:
        by_item_by_day[r['menu_item_id']][r['order__created_at__date']] = float(r['qty'])

    # Window dates grouped by weekday so empty days correctly pull the average
    # down (an item that sells 0 on most Mondays should forecast a low Monday).
    window_dates = [today - timedelta(days=i + 1) for i in range(FORECAST_LOOKBACK_DAYS)]
    dates_by_weekday = defaultdict(list)
    for d in window_dates:
        dates_by_weekday[d.weekday()].append(d)

    out = []
    for item_id, day_map in by_item_by_day.items():
        if not any(day_map.values()):
            continue
        weekday_avg = {}
        for wd, dates in dates_by_weekday.items():
            weekday_avg[wd] = mean([day_map.get(d, 0.0) for d in dates])
        for d_ahead in range(1, horizon_days + 1):
            target = today + timedelta(days=d_ahead)
            avg = weekday_avg.get(target.weekday(), 0.0)
            out.append({
                'menu_item_id': item_id,
                'date': target,
                'hour': None,
                'qty_p50': round(avg, 2),
                'qty_p90': round(avg * 1.4, 2),
            })
    return out


# ── Reorder ───────────────────────────────────────────────────────────────

DEFAULT_LEAD_TIME_DAYS = 3
DEFAULT_BUFFER_DAYS = 2


def reorder_baseline():
    """
    Without an ML forecast: use the existing low_stock_threshold as the
    trigger and suggest enough to cover (lead_time + buffer) days of the
    last-14-days consumption run-rate.
    """
    today = timezone.localdate()
    lookback_start = today - timedelta(days=14)
    horizon = DEFAULT_LEAD_TIME_DAYS + DEFAULT_BUFFER_DAYS

    # Compute consumption per inventory item from OrderItem × Recipe over 14 days.
    consumption = defaultdict(Decimal)
    paid_items = OrderItem.objects.filter(
        order__status='paid', order__created_at__date__gte=lookback_start,
    )
    # Direct-sale items: 1 OrderItem qty = 1 inventory unit.
    for oi in paid_items.filter(menu_item__inventory_item__isnull=False).values(
        'menu_item__inventory_item_id',
    ).annotate(qty=Sum('quantity')):
        consumption[oi['menu_item__inventory_item_id']] += Decimal(str(oi['qty']))
    # Recipe items: sum qty_required × quantity.
    for r in Recipe.objects.select_related('inventory_item').all():
        sold = paid_items.filter(menu_item_id=r.menu_item_id).aggregate(s=Sum('quantity'))['s'] or 0
        if sold:
            consumption[r.inventory_item_id] += r.quantity_required * Decimal(str(sold))

    out = []
    for inv in InventoryItem.objects.all():
        used = consumption.get(inv.pk, Decimal('0'))
        daily = used / Decimal('14')
        if daily <= 0:
            continue
        days_left = float(inv.stock_quantity / daily) if daily > 0 else 999.0
        if days_left >= horizon:
            continue
        target_qty = (daily * Decimal(horizon * 2)).quantize(Decimal('0.01'))  # 2× horizon
        gap = target_qty - inv.stock_quantity
        if gap <= 0:
            continue
        out.append({
            'inventory_item_id': inv.pk,
            'suggested_qty': gap,
            'needed_by': today + timedelta(days=max(0, int(days_left - DEFAULT_LEAD_TIME_DAYS))),
            'days_of_cover': round(days_left, 1),
            'reason': f'Baseline: 14-day run-rate {daily:.2f}/day, stock covers {days_left:.1f}d',
        })
    return out


# ── Anomaly ──────────────────────────────────────────────────────────────

def anomaly_baseline():
    """
    No real baseline for anomaly detection — needs distributional knowledge.
    Return [] so reports show "Not enough shifts yet, ML in X more."
    """
    return []


# ── Basket ───────────────────────────────────────────────────────────────

def basket_baseline():
    """
    Co-occurrence count fallback: just rank pairs by raw co-occurrence in
    the last 500 orders. No support/confidence/lift — set to None.
    """
    last_orders = Order.objects.filter(status='paid').order_by('-created_at')[:500]
    order_ids = list(last_orders.values_list('pk', flat=True))
    if not order_ids:
        return []

    pairs = defaultdict(int)
    items_by_order = defaultdict(set)
    for oi in OrderItem.objects.filter(order_id__in=order_ids).values('order_id', 'menu_item_id'):
        items_by_order[oi['order_id']].add(oi['menu_item_id'])
    for items in items_by_order.values():
        items = sorted(items)
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                pairs[(a, b)] += 1
                pairs[(b, a)] += 1

    if not pairs:
        return []
    n = len(items_by_order)
    out = []
    for (a, b), count in pairs.items():
        if count < 3:
            continue
        out.append({
            'antecedent_id': a,
            'consequent_id': b,
            'support': count / n,
            'confidence': count / max(1, sum(1 for items in items_by_order.values() if a in items)),
            'lift': 1.0,  # unknown without modelling — flat lift
            'n_orders': n,
        })
    out.sort(key=lambda r: r['support'], reverse=True)
    return out[:100]


# ── Menu engineering ─────────────────────────────────────────────────────

def menu_class_baseline(window_days=28):
    """
    Same logic as the ML version — menu engineering is statistical, not ML.
    Identical implementation; we still call this "baseline" so reports
    show the banner until enough data has accumulated.
    """
    return _menu_class_compute(window_days)


def _menu_class_compute(window_days):
    today = timezone.localdate()
    start = today - timedelta(days=window_days)
    paid_items = OrderItem.objects.filter(
        order__status='paid', order__created_at__date__gte=start,
    )

    agg = (
        paid_items.values('menu_item_id')
        .annotate(
            units=Sum('quantity'),
            revenue=Sum(F('unit_price') * F('quantity')),
            cost=Sum(F('unit_cost') * F('quantity')),
        )
    )
    agg = list(agg)
    if not agg:
        return []
    total_units = sum(r['units'] for r in agg) or 1

    rows = []
    for r in agg:
        units = r['units']
        revenue = Decimal(str(r['revenue'] or 0))
        cost = Decimal(str(r['cost'] or 0))
        margin = revenue - cost
        margin_pct = float(margin / revenue * 100) if revenue > 0 else 0.0
        popularity_pct = units / total_units * 100
        rows.append({
            'menu_item_id': r['menu_item_id'],
            'units_sold': units,
            'revenue': revenue.quantize(Decimal('0.01')),
            'margin': margin.quantize(Decimal('0.01')),
            'margin_pct': margin_pct,
            'popularity_pct': popularity_pct,
            'window_start': start,
            'window_end': today,
        })

    # Median splits → quadrants.
    if rows:
        sorted_pop = sorted(r['popularity_pct'] for r in rows)
        sorted_mar = sorted(r['margin_pct'] for r in rows)
        pop_median = sorted_pop[len(sorted_pop) // 2]
        mar_median = sorted_mar[len(sorted_mar) // 2]
        for r in rows:
            hi_pop = r['popularity_pct'] >= pop_median
            hi_mar = r['margin_pct'] >= mar_median
            if hi_pop and hi_mar:
                r['classification'] = 'star'
            elif hi_pop and not hi_mar:
                r['classification'] = 'plowhorse'
            elif not hi_pop and hi_mar:
                r['classification'] = 'puzzle'
            else:
                r['classification'] = 'dog'
    return rows
