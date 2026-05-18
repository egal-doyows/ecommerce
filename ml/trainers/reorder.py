"""
Reorder recommendations.

Takes the latest DemandForecast (or falls back to consumption run-rate) and
projects when each InventoryItem will run out of stock. Suggests a quantity
to bring stock up to 2× the lead-time horizon.
"""

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

from menu.models import InventoryItem, OrderItem, Recipe
from ml import fallbacks
from ml.models import DemandForecast, ReorderSuggestion
from ml.trainers._runner import model_run


LEAD_TIME_DEFAULT = 3   # fallback when no preferred_supplier or its lead_time is unknown
BUFFER_DAYS = 2
FORECAST_HORIZON = 7    # look ahead this far to compute daily run-rate


def _per_item_daily_demand_from_forecast():
    """
    Convert per-menu-item forecast → per-inventory-item daily demand.

    Averages the next FORECAST_HORIZON days of predicted quantities for each
    menu item, then attributes to the underlying inventory items via Recipe
    (or direct sale if the menu item is itself the stock unit).
    Returns {inventory_item_id: Decimal(daily_qty)}.
    """
    today = timezone.localdate()
    horizon_end = today + timedelta(days=FORECAST_HORIZON)
    forecasts = DemandForecast.objects.filter(
        date__gt=today, date__lte=horizon_end,
    ).select_related('menu_item__inventory_item')
    if not forecasts:
        return None

    per_inv_per_day = defaultdict(Decimal)
    for f in forecasts:
        qty = Decimal(str(f.qty_p50))
        mi = f.menu_item
        if mi.inventory_item_id:
            per_inv_per_day[mi.inventory_item_id] += qty / Decimal(FORECAST_HORIZON)
        else:
            recipes = Recipe.objects.filter(menu_item_id=mi.pk).select_related('inventory_item')
            for r in recipes:
                per_inv_per_day[r.inventory_item_id] += (
                    r.quantity_required * qty / Decimal(FORECAST_HORIZON)
                )
    return per_inv_per_day


def train():
    with model_run('reorder') as run:
        # Refresh learned lead times from goods-receipt history first, so
        # any supplier with ≥5 receipts uses the observed median rather
        # than the manual default.
        try:
            from supplier.learning import learn_lead_times
            learn_lead_times()
        except Exception as e:
            logger.warning('lead_time learning failed: %s', e)

        per_day = _per_item_daily_demand_from_forecast()
        if per_day is None:
            # No forecast → baseline.
            rows = fallbacks.reorder_baseline()
            _replace_open_suggestions(rows, source='baseline')
            run.rows_written = len(rows)
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = 'No DemandForecast rows yet. Using consumption baseline.'
            return

        today = timezone.localdate()
        rows = []
        # Per-supplier lead times — fetch the FK once.
        for inv in InventoryItem.objects.select_related('preferred_supplier').all():
            daily = per_day.get(inv.pk, Decimal('0'))
            if daily <= 0:
                continue

            lead_time = (
                inv.preferred_supplier.lead_time_days
                if inv.preferred_supplier_id and inv.preferred_supplier.lead_time_days
                else LEAD_TIME_DEFAULT
            )
            horizon = lead_time + BUFFER_DAYS

            days_left = float(inv.stock_quantity / daily) if daily > 0 else 999.0
            if days_left >= horizon:
                continue
            target_qty = (daily * Decimal(horizon * 2)).quantize(Decimal('0.01'))
            gap = target_qty - inv.stock_quantity
            if gap <= 0:
                continue
            rows.append({
                'inventory_item_id': inv.pk,
                'suggested_qty': gap,
                # Order this many days before stockout — supplier-specific.
                'needed_by': today + timedelta(days=max(0, int(days_left - lead_time))),
                'days_of_cover': round(days_left, 1),
                'reason': (
                    f'Forecast run-rate {daily:.2f}/day; stock covers '
                    f'{days_left:.1f}d; supplier lead time {lead_time}d'
                ),
            })
        _replace_open_suggestions(rows, source='ml')
        run.rows_used = len(per_day)
        run.rows_written = len(rows)


@transaction.atomic
def _replace_open_suggestions(rows, source):
    """Replace today's open ML/baseline suggestions; preserve user-actioned ones."""
    today = timezone.localdate()
    ReorderSuggestion.objects.filter(
        status='open', computed_at__date=today,
    ).delete()
    objs = [
        ReorderSuggestion(
            inventory_item_id=r['inventory_item_id'],
            suggested_qty=r['suggested_qty'],
            needed_by=r['needed_by'],
            days_of_cover=r['days_of_cover'],
            reason=r['reason'],
            source=source,
            status='open',
        )
        for r in rows
    ]
    ReorderSuggestion.objects.bulk_create(objs, batch_size=200)
