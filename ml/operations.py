"""Derived operational intelligence built only from existing business data.

These helpers deliberately create no new operational records. They turn the
current POS, recipe, stock, purchasing and channel history into actions while
the core ML trainers continue to own persisted predictions.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from menu.models import InventoryItem, Order, OrderItem, Recipe, StockAdjustment
from ml.models import BasketRule, DemandForecast
from receiving.models import GoodsReceiptItem
from staff_meals.models import StaffMealItem
from waste.models import WasteItem


WINDOW_DAYS = 28


def prep_and_waste():
    """Tomorrow's forecast with the recent waste cost that warrants caution."""
    today = timezone.localdate()
    target = today + timedelta(days=1)
    forecasts = DemandForecast.objects.filter(
        date=target, hour__isnull=True,
    ).select_related('menu_item').order_by('-qty_p50')[:30]
    waste_cost = sum(
        (row.quantity * row.unit_cost for row in WasteItem.objects.filter(
            waste_log__date__gte=today - timedelta(days=WINDOW_DAYS),
        )), Decimal('0'),
    )
    return {
        'date': target,
        'rows': forecasts,
        'recent_waste_cost': waste_cost,
        'window_days': WINDOW_DAYS,
    }


def recipe_variance():
    """Show known non-sales usage beside recipe-derived expected consumption.

    A physical-count delta is not an exact consumption measurement, so the
    result is deliberately labelled as *known non-sales usage*, not shrinkage.
    """
    today = timezone.localdate()
    start = today - timedelta(days=WINDOW_DAYS)
    expected = defaultdict(Decimal)
    sold = (
        OrderItem.objects.filter(order__status='paid', order__created_at__date__gte=start)
        .values('menu_item_id').annotate(qty=Sum('quantity'))
    )
    recipes = defaultdict(list)
    for recipe in Recipe.objects.select_related('inventory_item').all():
        recipes[recipe.menu_item_id].append(recipe)
    for row in sold:
        for recipe in recipes.get(row['menu_item_id'], []):
            expected[recipe.inventory_item_id] += recipe.quantity_required * row['qty']

    non_sales = defaultdict(Decimal)
    for row in WasteItem.objects.filter(waste_log__date__gte=start):
        non_sales[row.inventory_item_id] += row.quantity
    for row in StaffMealItem.objects.filter(staff_meal_log__date__gte=start):
        if row.inventory_item_id:
            non_sales[row.inventory_item_id] += row.quantity
    for row in StockAdjustment.objects.filter(created_at__date__gte=start, qty_delta__lt=0):
        non_sales[row.inventory_item_id] += -row.qty_delta

    inventory = InventoryItem.objects.in_bulk(set(expected) | set(non_sales))
    rows = []
    for item_id in set(expected) | set(non_sales):
        base = expected[item_id]
        overhead = non_sales[item_id]
        if base <= 0 and overhead <= 0:
            continue
        rows.append({
            'item': inventory[item_id], 'expected_qty': base,
            'non_sales_qty': overhead,
            'non_sales_pct': float(overhead / base * 100) if base else None,
        })
    return sorted(rows, key=lambda row: row['non_sales_qty'], reverse=True)[:30]


def supplier_intelligence():
    """Observed lead time, fulfilment, and price direction by supplier."""
    stats = defaultdict(lambda: {
        'supplier': None, 'lead_days': [], 'ordered': Decimal('0'),
        'received': Decimal('0'), 'prices': defaultdict(list),
    })
    for row in GoodsReceiptItem.objects.select_related(
        'receipt__purchase_order__supplier', 'po_item__inventory_item',
    ):
        po = row.receipt.purchase_order
        bucket = stats[po.supplier_id]
        bucket['supplier'] = po.supplier
        bucket['ordered'] += row.po_item.quantity
        bucket['received'] += row.received_quantity
        bucket['prices'][row.po_item.inventory_item.name].append(row.po_item.unit_price)
        if row.receipt.received_date and po.order_date:
            bucket['lead_days'].append((row.receipt.received_date - po.order_date).days)

    rows = []
    for bucket in stats.values():
        prices = [p for values in bucket['prices'].values() for p in values]
        first, last = (prices[0], prices[-1]) if len(prices) > 1 else (None, None)
        rows.append({
            'supplier': bucket['supplier'],
            'observed_lead_days': round(sum(bucket['lead_days']) / len(bucket['lead_days']), 1)
            if bucket['lead_days'] else None,
            'fill_rate': float(bucket['received'] / bucket['ordered'] * 100)
            if bucket['ordered'] else None,
            'price_change_pct': float((last - first) / first * 100)
            if first and last and first else None,
        })
    return sorted(rows, key=lambda row: (row['fill_rate'] is None, row['fill_rate'] or 0))


def margin_upsells():
    """Rank learned basket pairs by confidence, margin, and stock availability."""
    rows = []
    for rule in BasketRule.objects.select_related('antecedent', 'consequent__inventory_item')[:200]:
        item = rule.consequent
        margin = max(Decimal('0'), item.price - item.current_unit_cost())
        in_stock = not item.inventory_item_id or item.inventory_item.stock_quantity > item.inventory_item.low_stock_threshold
        if not in_stock:
            continue
        score = float(rule.confidence * rule.lift * margin)
        rows.append({'rule': rule, 'margin': margin, 'score': score})
    return sorted(rows, key=lambda row: row['score'], reverse=True)[:20]


def staffing_signal():
    """Forecasted busy days plus the historically busiest service hours."""
    today = timezone.localdate()
    by_day = defaultdict(float)
    for row in DemandForecast.objects.filter(date__gt=today, hour__isnull=True):
        by_day[row.date] += row.qty_p50
    hourly = defaultdict(int)
    for created in Order.objects.filter(
        status='paid', created_at__date__gte=today - timedelta(days=WINDOW_DAYS),
    ).values_list('created_at', flat=True):
        hourly[timezone.localtime(created).hour] += 1
    return {
        'days': [
            {'date': date, 'forecast_qty': qty}
            for date, qty in sorted(by_day.items(), key=lambda row: row[1], reverse=True)[:7]
        ],
        'hours': [
            {'hour': hour, 'orders': count}
            for hour, count in sorted(hourly.items(), key=lambda row: row[1], reverse=True)[:4]
        ],
        'window_days': WINDOW_DAYS,
    }


def channel_profitability():
    """Net contribution by current sales channel after configured commission."""
    from menu.models import RestaurantSettings

    settings = RestaurantSettings.load()
    commission = {
        'ubereats': settings.ubereats_commission_pct,
        'glovo': settings.glovo_commission_pct,
        'bolt': settings.bolt_commission_pct,
        'jumia': settings.jumia_commission_pct,
    }
    rows = defaultdict(lambda: {'revenue': Decimal('0'), 'cost': Decimal('0')})
    for order in Order.objects.filter(status='paid').prefetch_related('items'):
        bucket = rows[order.source]
        bucket['revenue'] += order.get_total()
        bucket['cost'] += sum((item.get_cost_subtotal() for item in order.items.all()), Decimal('0'))
    result = []
    for source, values in rows.items():
        pct = commission.get(source, Decimal('0'))
        fee = values['revenue'] * pct / Decimal('100')
        result.append({
            'source': source, 'revenue': values['revenue'], 'commission': fee,
            'contribution': values['revenue'] - values['cost'] - fee,
        })
    return sorted(result, key=lambda row: row['contribution'], reverse=True)


def readiness():
    return [
        {
            'name': 'Customer retention and offers',
            'ready': False,
            'need': 'Customer or loyalty identity on orders, consent, and repeat-visit history.',
        },
        {
            'name': 'Price and promotion optimization',
            'ready': False,
            'need': 'Historical price changes, promotion exposure, and controlled offer outcomes.',
        },
    ]
