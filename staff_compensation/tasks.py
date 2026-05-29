"""
Celery tasks for staff compensation & operational alerts.

Referenced by CELERY_BEAT_SCHEDULE in config/settings/base.py:
  - staff_compensation.tasks.generate_monthly_payment_records  (1st of month)
  - staff_compensation.tasks.send_low_stock_alerts             (daily 08:00)

Task names are pinned explicitly to the dotted paths the beat schedule uses,
so registration can't drift from the schedule.
"""
import logging

from celery import shared_task
from django.contrib.auth.models import Group, User
from django.core.mail import EmailMultiAlternatives
from django.db.models import F

logger = logging.getLogger(__name__)


@shared_task(name='staff_compensation.tasks.generate_monthly_payment_records')
def generate_monthly_payment_records():
    """Roll up commission PaymentRecords for every commission-based staff member.

    Backfills any missing past months and refreshes the current-month pending
    record. Mirrors what the compensation-detail page does lazily, but on a
    schedule so payouts exist even if nobody opens the page.
    """
    from .models import generate_current_month_record, generate_past_month_records

    processed = 0
    staff = User.objects.filter(
        is_active=True, compensation__compensation_type='commission',
    )
    for user in staff:
        try:
            generate_past_month_records(user)
            generate_current_month_record(user)
            processed += 1
        except Exception:
            logger.exception('payment-record generation failed for user %s', user.pk)
    return processed


def _alert_recipients():
    """Superusers + Manager-group members with a non-empty email."""
    manager = Group.objects.filter(name='Manager').first()
    qs = User.objects.filter(is_active=True, is_superuser=True).exclude(email='')
    if manager is not None:
        qs = qs.union(
            User.objects.filter(is_active=True, groups=manager).exclude(email=''),
        )
    return list(qs)


@shared_task(name='staff_compensation.tasks.send_low_stock_alerts')
def send_low_stock_alerts():
    """Email managers a list of inventory items at/below their low-stock threshold."""
    from menu.models import InventoryItem

    low = list(
        InventoryItem.objects
        .filter(stock_quantity__lte=F('low_stock_threshold'))
        .order_by('name')
    )
    if not low:
        return 0

    recipients = _alert_recipients()
    if not recipients:
        logger.warning('low-stock alert: %d item(s) low but no manager recipients', len(low))
        return 0

    lines = [
        f"- {i.name}: {i.stock_quantity} {i.unit} (threshold {i.low_stock_threshold})"
        for i in low
    ]
    subject = f"Low-stock alert — {len(low)} item{'s' if len(low) != 1 else ''}"
    body = (
        "The following items are at or below their low-stock threshold:\n\n"
        + "\n".join(lines)
    )

    sent = 0
    for user in recipients:
        msg = EmailMultiAlternatives(subject=subject, body=body, to=[user.email])
        try:
            msg.send(fail_silently=False)
            sent += 1
        except Exception:
            # One bad address shouldn't kill the batch.
            logger.warning('low-stock alert send failed for %s', user.email)
    return sent
