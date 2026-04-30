"""Celery tasks for staff compensation background processing."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_payment_records():
    """
    Generate payment records for all commission-earning staff.
    Scheduled to run monthly via Celery Beat.
    """
    from django.contrib.auth.models import User
    from .models import generate_past_month_records, generate_current_month_record

    users = User.objects.filter(
        is_active=True,
        compensation__compensation_type__in=['commission', 'both'],
    ).select_related('compensation')

    created_count = 0
    for user in users:
        try:
            generate_past_month_records(user)
            record = generate_current_month_record(user)
            if record:
                created_count += 1
        except Exception:
            logger.exception("Failed to generate payment record for user=%s", user.username)

    logger.info("Generated/updated %d payment records", created_count)
    return created_count


@shared_task
def send_low_stock_alerts():
    """Check for low stock items and log alerts."""
    from django.db.models import F
    from menu.models import InventoryItem

    low_items = InventoryItem.objects.filter(
        stock_quantity__lte=F('low_stock_threshold'),
    )

    if low_items.exists():
        items_list = ', '.join(f'{i.name} ({i.stock_quantity})' for i in low_items[:20])
        logger.warning("Low stock alert: %s", items_list)

    return low_items.count()
