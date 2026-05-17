"""
Data-sufficiency gates.

Each trainer asks: do I have enough data to bother fitting a real model?
If not, the caller falls back to a baseline in `ml.fallbacks`.

Thresholds are deliberately conservative — we'd rather show "baseline" with
sensible numbers than overfit to two weeks of noisy data.
"""

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from menu.models import Order, OrderItem, Shift


# Minimum data points to consider running the real model.
# Tunable from one place — keep the rest of the codebase agnostic.
GATES = {
    'forecast': {
        'min_orders_total': 200,
        'min_days_with_orders': 42,    # ~6 weeks of activity
        'min_orders_for_item': 30,     # per item — items below get baseline
    },
    'reorder': {
        # Inherits forecast gate — reorder only runs if forecast has ML output.
        'min_days_with_orders': 42,
    },
    'anomaly': {
        'min_shifts_per_user': 30,
        'min_total_shifts': 60,
    },
    'basket': {
        'min_orders': 500,
        'min_multi_item_orders': 200,
    },
    'menu_class': {
        'min_orders': 200,
        'min_window_days': 28,
    },
}


def _orders_in_last(days):
    cutoff = timezone.now() - timedelta(days=days)
    return Order.objects.filter(created_at__gte=cutoff, status='paid')


def forecast_ready():
    g = GATES['forecast']
    qs = Order.objects.filter(status='paid')
    n = qs.count()
    days = qs.dates('created_at', 'day').count()
    return (n >= g['min_orders_total'] and days >= g['min_days_with_orders'], {
        'orders': n,
        'days_with_orders': days,
        'need_orders': g['min_orders_total'],
        'need_days': g['min_days_with_orders'],
    })


def item_forecast_ready(menu_item_id):
    g = GATES['forecast']
    n = OrderItem.objects.filter(
        menu_item_id=menu_item_id, order__status='paid',
    ).count()
    return n >= g['min_orders_for_item'], n


def anomaly_ready():
    g = GATES['anomaly']
    n = Shift.objects.filter(is_active=False).count()
    return n >= g['min_total_shifts'], {'closed_shifts': n, 'need': g['min_total_shifts']}


def user_anomaly_ready(user_id):
    g = GATES['anomaly']
    n = Shift.objects.filter(waiter_id=user_id, is_active=False).count()
    return n >= g['min_shifts_per_user'], n


def basket_ready():
    g = GATES['basket']
    paid = Order.objects.filter(status='paid')
    n_total = paid.count()
    # Approximate multi-item-orders cheaply — exact count would scan OrderItem.
    n_multi = paid.annotate(_ic=Count('items')).filter(_ic__gte=2).count()
    return (n_total >= g['min_orders'] and n_multi >= g['min_multi_item_orders'], {
        'orders': n_total,
        'multi_item_orders': n_multi,
        'need_orders': g['min_orders'],
        'need_multi': g['min_multi_item_orders'],
    })


def menu_class_ready():
    g = GATES['menu_class']
    cutoff = timezone.now() - timedelta(days=g['min_window_days'])
    n = Order.objects.filter(status='paid', created_at__gte=cutoff).count()
    return n >= g['min_orders'], {'orders_in_window': n, 'need': g['min_orders']}
