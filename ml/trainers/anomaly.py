"""
Per-staff anomaly detection on shift-level metrics.

Computes simple z-scores against each user's own historical baseline
(more interpretable than IsolationForest at this data volume, and easier
to explain to managers). Flags shifts where any metric is > 2σ from the
person's own mean.

Metrics watched per shift:
  - cash_variance        (counted_cash - expected_cash) / expected
  - voids_per_shift      cancelled orders / shift
  - comps_per_shift      is_comp=True orders / shift
  - discount_pct         sum(discount_amount) / sum(get_total)
"""

import logging
import statistics
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from menu.models import Order, OrderItem, Shift
from ml import gates
from ml.models import AnomalyEvent
from ml.trainers._runner import model_run

logger = logging.getLogger(__name__)

Z_THRESHOLD = 2.0
LOOKBACK_DAYS = 90


def _shift_metrics(shift):
    """Return dict of metric → value for a closed shift, or None if unavailable."""
    orders = shift.orders.all()
    paid = orders.filter(status='paid')
    n_paid = paid.count()
    if n_paid == 0:
        return None

    sales = sum(o.get_total() for o in paid) or Decimal('0')
    discount_total = paid.aggregate(s=Sum('discount_amount'))['s'] or Decimal('0')
    voids = orders.filter(status='cancelled').count()
    comps = paid.filter(is_comp=True).count()

    expected_cash = (
        Decimal(str(shift.starting_cash)) +
        sum(o.get_total() for o in paid.filter(payment_method='cash'))
    )
    if shift.counted_cash is None or expected_cash == 0:
        cash_variance = 0.0
    else:
        cash_variance = float((Decimal(str(shift.counted_cash)) - expected_cash) / expected_cash)

    discount_pct = float(discount_total / sales) if sales > 0 else 0.0

    return {
        'cash_variance': cash_variance,
        'voids_per_shift': float(voids),
        'comps_per_shift': float(comps),
        'discount_pct': discount_pct,
    }


def _z(value, sample):
    """Z-score of `value` against `sample` (list of floats). 0 if undefined."""
    if len(sample) < 5:
        return 0.0, 0.0
    mean = statistics.mean(sample)
    sd = statistics.pstdev(sample)
    if sd == 0:
        return 0.0, mean
    return (value - mean) / sd, mean


def train():
    with model_run('anomaly') as run:
        ready, info = gates.anomaly_ready()
        run.rows_used = info['closed_shifts']
        if not ready:
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = (
                f"Not enough closed shifts: {info['closed_shifts']}/{info['need']}."
            )
            return

        cutoff = timezone.now() - timedelta(days=LOOKBACK_DAYS)
        recent_shifts = (
            Shift.objects.filter(is_active=False, started_at__gte=cutoff)
            .select_related('waiter')
            .order_by('-started_at')
        )

        # Per-user metric history for baselining.
        history = defaultdict(lambda: defaultdict(list))
        for s in recent_shifts:
            m = _shift_metrics(s)
            if m is None:
                continue
            for k, v in m.items():
                history[s.waiter_id][k].append(v)

        # Only flag the most recent shift per user (we re-run daily).
        latest_per_user = {}
        for s in recent_shifts:
            if s.waiter_id not in latest_per_user:
                latest_per_user[s.waiter_id] = s

        events = []
        for user_id, shift in latest_per_user.items():
            user_ready, n = gates.user_anomaly_ready(user_id)
            if not user_ready:
                continue
            m = _shift_metrics(shift)
            if m is None:
                continue
            today = shift.started_at.date()

            for metric, value in m.items():
                # Compare against same user's history excluding the current shift.
                sample = [
                    h for h in history[user_id][metric] if h != value
                ] or history[user_id][metric]
                z, baseline = _z(value, sample)
                if abs(z) < Z_THRESHOLD:
                    continue
                events.append(AnomalyEvent(
                    subject_type='user',
                    subject_id=user_id,
                    subject_label=shift.waiter.get_username(),
                    metric=metric,
                    shift=shift,
                    observed_value=value,
                    expected_value=baseline,
                    z_score=abs(z),
                    direction='high' if z > 0 else 'low',
                    occurred_on=today,
                    source='ml',
                ))

        _write_events(events)
        run.rows_written = len(events)
        run.metric_name = 'flagged'
        run.metric_value = float(len(events))


@transaction.atomic
def _write_events(events):
    """
    Insert new events; skip duplicates of (subject, metric, shift) already on file.
    """
    keys_existing = set(AnomalyEvent.objects.values_list(
        'subject_type', 'subject_id', 'metric', 'shift_id',
    ))
    fresh = [
        e for e in events
        if (e.subject_type, e.subject_id, e.metric, e.shift_id) not in keys_existing
    ]
    AnomalyEvent.objects.bulk_create(fresh, batch_size=200)
