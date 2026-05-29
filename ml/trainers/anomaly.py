"""
Per-staff anomaly detection on shift-level metrics.

Computes simple z-scores against each user's own historical baseline
(more interpretable than IsolationForest at this data volume, and easier
to explain to managers). Flags shifts where any metric is > 2σ from the
person's own mean.

Per-waiter (server) metrics:
  - cash_variance        (counted_cash - expected_cash) / expected
  - voids_per_shift      cancelled orders / shift
  - comps_per_shift      is_comp=True orders / shift
  - discount_pct         sum(discount_amount) / sum(get_total)
  - combined_loss_risk   sqrt(z_cash² + z_voids²); only flagged when
                         both individual z's are >1σ so the joint
                         signal doesn't duplicate per-metric flags

Per-supervisor metrics (subject = the user who counted the till):
  - supervisor_cash_variance  same variance, attributed to whoever
                              counted (catches lax counters / pairs
                              where variance only spikes with specific
                              supervisor + server combos)
  - count_latency_minutes     pending_close_at → counted_at gap.
                              Long gaps = cash sat un-counted.
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

POST_HOC_BUFFER = timedelta(minutes=1)

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

    # Match the z-report reconciliation: opening + cash sales − cash refunds.
    # (Comps contribute 0 via get_total, so they're already excluded.)
    cash_sales = sum(o.get_total() for o in paid.filter(payment_method='cash'))
    cash_refunds = sum(
        o.get_total()
        for o in orders.filter(status='cancelled', payment_method='cash')
    )
    expected_cash = Decimal(str(shift.starting_cash)) + cash_sales - cash_refunds
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


def _supervisor_shift_metrics(shift):
    """Per-supervisor metrics for a shift they counted.

    Returns None if the shift wasn't recorded through the supervisor
    flow (no counted_by, no pending_close_at, or no counted_at).
    """
    if not shift.counted_by_id or shift.counted_at is None or shift.pending_close_at is None:
        return None
    base = _shift_metrics(shift)
    if base is None:
        return None
    latency = (shift.counted_at - shift.pending_close_at).total_seconds() / 60.0
    return {
        'supervisor_cash_variance': base['cash_variance'],
        'count_latency_minutes': float(latency),
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

        # Audit-edit detection runs unconditionally — it doesn't need a
        # baseline; any post-hoc edit is on its own a signal. Collect
        # those first so they fire even when the shift-baseline gate
        # fails.
        cutoff = timezone.now() - timedelta(days=LOOKBACK_DAYS)
        events = list(_post_hoc_edit_events(window_start=cutoff))

        if not ready:
            _write_events(events)
            run.rows_written = len(events)
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = (
                f"Not enough closed shifts: {info['closed_shifts']}/{info['need']}."
            )
            return

        recent_shifts = list(
            Shift.objects.filter(is_active=False, started_at__gte=cutoff)
            .select_related('waiter', 'counted_by')
            .order_by('-started_at')
        )

        # Per-waiter metric history.
        history = defaultdict(lambda: defaultdict(list))
        for s in recent_shifts:
            m = _shift_metrics(s)
            if m is None:
                continue
            for k, v in m.items():
                history[s.waiter_id][k].append(v)

        # Per-supervisor metric history (only shifts they counted).
        sup_history = defaultdict(lambda: defaultdict(list))
        for s in recent_shifts:
            sm = _supervisor_shift_metrics(s)
            if sm is None:
                continue
            for k, v in sm.items():
                sup_history[s.counted_by_id][k].append(v)

        # Only flag the most recent shift per user/supervisor (we re-run daily).
        latest_per_user = {}
        for s in recent_shifts:
            if s.waiter_id not in latest_per_user:
                latest_per_user[s.waiter_id] = s
        latest_per_supervisor = {}
        for s in recent_shifts:
            if s.counted_by_id and s.counted_by_id not in latest_per_supervisor:
                latest_per_supervisor[s.counted_by_id] = s

        # `events` already contains the post-hoc audit-edit events from the
        # pre-gate block; baseline-driven metrics extend it from here.

        # ── Waiter-attributed metrics + joint loss risk ──────────────────
        for user_id, shift in latest_per_user.items():
            user_ready, _ = gates.user_anomaly_ready(user_id)
            if not user_ready:
                continue
            m = _shift_metrics(shift)
            if m is None:
                continue
            today = shift.started_at.date()

            # Track the cash / voids z-scores so we can compute joint risk
            # without re-running _z. None means below the per-metric flag
            # threshold; we still need the raw z to combine, so re-compute.
            z_cash = z_voids = 0.0
            for metric, value in m.items():
                sample = [h for h in history[user_id][metric] if h != value] or history[user_id][metric]
                z, baseline = _z(value, sample)
                if metric == 'cash_variance':
                    z_cash = z
                elif metric == 'voids_per_shift':
                    z_voids = z
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

            # Joint risk: flag only when BOTH dimensions are at least mildly
            # elevated AND the magnitude crosses Z_THRESHOLD. This is the
            # case the per-metric loop misses (e.g., z_cash=1.5 + z_voids=1.5
            # → joint=2.12 ≥ 2.0). If one is already flagged solo, the joint
            # adds no new info and we skip it.
            if abs(z_cash) > 1.0 and abs(z_voids) > 1.0:
                joint = (z_cash ** 2 + z_voids ** 2) ** 0.5
                already_solo = abs(z_cash) >= Z_THRESHOLD or abs(z_voids) >= Z_THRESHOLD
                if joint >= Z_THRESHOLD and not already_solo:
                    events.append(AnomalyEvent(
                        subject_type='user',
                        subject_id=user_id,
                        subject_label=shift.waiter.get_username(),
                        metric='combined_loss_risk',
                        shift=shift,
                        observed_value=joint,
                        expected_value=0.0,
                        z_score=joint,
                        direction='high',
                        occurred_on=today,
                        source='ml',
                    ))

        # ── Supervisor-attributed metrics ────────────────────────────────
        for sup_id, shift in latest_per_supervisor.items():
            sup_ready, _ = gates.supervisor_anomaly_ready(sup_id)
            if not sup_ready:
                continue
            sm = _supervisor_shift_metrics(shift)
            if sm is None:
                continue
            today = (shift.counted_at or shift.started_at).date()

            for metric, value in sm.items():
                sample = [h for h in sup_history[sup_id][metric] if h != value] or sup_history[sup_id][metric]
                z, baseline = _z(value, sample)
                if abs(z) < Z_THRESHOLD:
                    continue
                events.append(AnomalyEvent(
                    subject_type='user',
                    subject_id=sup_id,
                    subject_label=shift.counted_by.get_username(),
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


def _post_hoc_edit_events(window_start):
    """One AnomalyEvent per actor who edited an Order or Shift audited
    field after the object was finalised. Count = number of such edits in
    the lookback window."""
    from collections import defaultdict
    from django.contrib.auth.models import User
    from django.contrib.contenttypes.models import ContentType
    try:
        from auditlog.models import LogEntry
    except ImportError:
        return []

    order_ct = ContentType.objects.get_for_model(Order)
    shift_ct = ContentType.objects.get_for_model(Shift)

    per_actor = defaultdict(lambda: {'count': 0, 'shift_id': None})
    order_meta = {}  # object_pk -> (voided_at, shift_id)
    shift_meta = {}  # object_pk -> counted_at

    entries = LogEntry.objects.filter(
        content_type__in=[order_ct, shift_ct],
        action=LogEntry.Action.UPDATE,
        timestamp__gte=window_start,
        actor__isnull=False,
    ).values('content_type_id', 'object_pk', 'actor_id', 'timestamp')

    for entry in entries:
        if entry['content_type_id'] == order_ct.id:
            if entry['object_pk'] not in order_meta:
                try:
                    o = Order.objects.only('voided_at', 'shift_id').get(pk=entry['object_pk'])
                    order_meta[entry['object_pk']] = (o.voided_at, o.shift_id)
                except Order.DoesNotExist:
                    order_meta[entry['object_pk']] = (None, None)
            finalised_at, shift_id = order_meta[entry['object_pk']]
        else:
            if entry['object_pk'] not in shift_meta:
                try:
                    s = Shift.objects.only('counted_at').get(pk=entry['object_pk'])
                    shift_meta[entry['object_pk']] = s.counted_at
                except Shift.DoesNotExist:
                    shift_meta[entry['object_pk']] = None
            finalised_at = shift_meta[entry['object_pk']]
            shift_id = int(entry['object_pk'])

        if finalised_at and entry['timestamp'] > finalised_at + POST_HOC_BUFFER:
            per_actor[entry['actor_id']]['count'] += 1
            if shift_id:
                per_actor[entry['actor_id']]['shift_id'] = shift_id

    if not per_actor:
        return []

    today = timezone.now().date()
    actors = {u.pk: u.username for u in User.objects.filter(pk__in=per_actor).only('username')}
    out = []
    for actor_id, info in per_actor.items():
        if actor_id not in actors:
            continue
        out.append(AnomalyEvent(
            subject_type='user',
            subject_id=actor_id,
            subject_label=actors[actor_id],
            metric='post_hoc_audit_edits',
            shift_id=info['shift_id'],
            observed_value=float(info['count']),
            expected_value=0.0,
            z_score=float(info['count']),  # raw count drives sort order
            direction='high',
            occurred_on=today,
            source='ml',
        ))
    return out


@transaction.atomic
def _write_events(events):
    """
    Insert new events; skip duplicates of (subject, metric, shift, day)
    already on file. occurred_on is part of the dedup key so a recurring
    daily-flagged issue still emits a fresh row each day.
    """
    keys_existing = set(AnomalyEvent.objects.values_list(
        'subject_type', 'subject_id', 'metric', 'shift_id', 'occurred_on',
    ))
    fresh = [
        e for e in events
        if (e.subject_type, e.subject_id, e.metric, e.shift_id, e.occurred_on)
        not in keys_existing
    ]
    AnomalyEvent.objects.bulk_create(fresh, batch_size=200)
