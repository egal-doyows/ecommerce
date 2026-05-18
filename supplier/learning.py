"""
Auto-learn supplier lead times from goods-receipt history.

For each supplier, compute observed lead time per PO as
    (first goods-receipt date) − (PO order_date)

Once ≥ MIN_OBSERVATIONS receipts exist for a supplier, update
Supplier.lead_time_days to the median observed value and flip
lead_time_source to 'learned'. Suppliers without enough data stay
on whatever the manager typed in.
"""

import logging
import statistics
from collections import defaultdict

from django.db.models import Min

from .models import Supplier

logger = logging.getLogger(__name__)

MIN_OBSERVATIONS = 5
LOOKBACK_DAYS = 180  # only learn from the last 6 months — older data is stale


def learn_lead_times():
    """Refresh learned lead_time_days for every eligible supplier.

    Returns dict {supplier_id: (old_value, new_value, n_observations)} for
    every supplier that was updated. Safe to call repeatedly; idempotent."""
    from datetime import timedelta
    from django.utils import timezone
    from purchasing.models import PurchaseOrder
    from receiving.models import GoodsReceipt

    cutoff = timezone.localdate() - timedelta(days=LOOKBACK_DAYS)
    # First receipt per PO is the lead-time signal — later receipts are
    # follow-ups for partial deliveries and don't represent supplier speed.
    earliest = (
        GoodsReceipt.objects
        .filter(received_date__gte=cutoff)
        .values('purchase_order_id')
        .annotate(first_received=Min('received_date'))
    )
    earliest_by_po = {row['purchase_order_id']: row['first_received'] for row in earliest}

    if not earliest_by_po:
        return {}

    pos = (
        PurchaseOrder.objects
        .filter(pk__in=earliest_by_po)
        .values('pk', 'supplier_id', 'order_date')
    )

    observations_by_supplier = defaultdict(list)
    for po in pos:
        first_received = earliest_by_po.get(po['pk'])
        if not first_received or not po['order_date']:
            continue
        days = (first_received - po['order_date']).days
        if days < 0:
            continue  # likely backdated PO; ignore
        observations_by_supplier[po['supplier_id']].append(days)

    updated = {}
    for supplier in Supplier.objects.filter(pk__in=observations_by_supplier):
        obs = observations_by_supplier[supplier.pk]
        if len(obs) < MIN_OBSERVATIONS:
            continue
        new_value = max(1, int(round(statistics.median(obs))))
        if supplier.lead_time_days == new_value and supplier.lead_time_source == 'learned':
            continue
        old = supplier.lead_time_days
        supplier.lead_time_days = new_value
        supplier.lead_time_source = 'learned'
        supplier.save(update_fields=['lead_time_days', 'lead_time_source'])
        updated[supplier.pk] = (old, new_value, len(obs))
        logger.info(
            'Learned lead_time for supplier=%s: %d → %d days (n=%d)',
            supplier.name, old, new_value, len(obs),
        )
    return updated
