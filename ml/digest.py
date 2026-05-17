"""
Daily ML digest email.

Pulls the actionable bits out of the ML tables so managers don't have to
open the dashboard every morning to see if anything needs their attention.
The digest is intentionally short — if a section is empty it's omitted.
If every section is empty, no email is sent at all (no noise).

Recipients: superusers + members of the `Manager` group with email set.
Same access scope as the dashboard, so anyone who can act on the data
gets the email.
"""

from datetime import timedelta
from typing import Iterable

from django.contrib.auth.models import Group, User
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from menu.cache import get_restaurant_settings
from ml.models import (
    AnomalyEvent, BasketRule, DemandForecast, ModelRun, ReorderSuggestion,
)


PREP_TOP_N = 8
REORDER_TOP_N = 10
EXCEPTION_TOP_N = 5
EXCEPTION_LOOKBACK_DAYS = 7


def _recipients() -> list[User]:
    """Superusers + Manager group members with a non-empty email."""
    manager = Group.objects.filter(name='Manager').first()
    qs = User.objects.filter(is_active=True).exclude(email='')
    if manager is not None:
        qs = qs.filter(
            is_superuser=True,
        ).union(
            User.objects.filter(is_active=True, groups=manager).exclude(email=''),
        )
    else:
        qs = qs.filter(is_superuser=True)
    return list(qs)


def build_digest_context(base_url: str | None = None) -> dict:
    """
    Gather the digest payload. `base_url` is the absolute URL prefix
    (e.g. https://beanandbite.co.ke) so emailed links work outside the
    request cycle. Falls back to '' so links degrade to relative paths.
    """
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    week_ago = today - timedelta(days=EXCEPTION_LOOKBACK_DAYS)

    prep_rows = list(
        DemandForecast.objects
        .filter(date=tomorrow, hour__isnull=True)
        .select_related('menu_item__category')
        .order_by('-qty_p50')[:PREP_TOP_N]
    )

    reorder_rows = list(
        ReorderSuggestion.objects
        .filter(status='open')
        .select_related('inventory_item__preferred_supplier')
        .order_by('needed_by')[:REORDER_TOP_N]
    )
    reorder_total = ReorderSuggestion.objects.filter(status='open').count()

    exception_rows = list(
        AnomalyEvent.objects
        .filter(dismissed=False, occurred_on__gte=week_ago)
        .select_related('shift__waiter')
        .order_by('-z_score')[:EXCEPTION_TOP_N]
    )
    exception_total = AnomalyEvent.objects.filter(
        dismissed=False, occurred_on__gte=week_ago,
    ).count()

    top_upsell = (
        BasketRule.objects
        .select_related('antecedent', 'consequent')
        .order_by('-lift', '-confidence')
        .first()
    )

    # One-row-per-trainer health card.
    health = []
    for name in ('forecast', 'reorder', 'anomaly', 'basket', 'menu_class'):
        run = ModelRun.objects.filter(model_name=name).first()
        if run:
            health.append({
                'name': name,
                'status': run.get_status_display(),
                'ok': run.status == 'ok',
                'when': run.started_at,
            })

    has_any = bool(prep_rows or reorder_rows or exception_rows or top_upsell)

    settings_obj = get_restaurant_settings()
    return {
        'has_any': has_any,
        'cafe_name': (settings_obj.name if settings_obj else 'Bean & Bite'),
        'today': today,
        'tomorrow': tomorrow,
        'prep_rows': prep_rows,
        'reorder_rows': reorder_rows,
        'reorder_total': reorder_total,
        'exception_rows': exception_rows,
        'exception_total': exception_total,
        'exception_window_days': EXCEPTION_LOOKBACK_DAYS,
        'top_upsell': top_upsell,
        'health': health,
        'dashboard_url': f"{base_url or ''}{reverse('ml-index')}",
        'reorders_url': f"{base_url or ''}{reverse('ml-reorders')}",
        'exceptions_url': f"{base_url or ''}{reverse('ml-exceptions')}",
        'prep_url': f"{base_url or ''}{reverse('ml-prep-list')}",
    }


def send_daily_digest(base_url: str | None = None) -> int:
    """
    Build the digest and email it to the recipient list.
    Returns the number of emails sent. 0 if nothing actionable today.
    """
    ctx = build_digest_context(base_url=base_url)
    if not ctx['has_any']:
        return 0

    recipients = _recipients()
    if not recipients:
        return 0

    subject = f"{ctx['cafe_name']} — Insights for {ctx['today']:%a %d %b}"
    html_body = render_to_string('ml/email/daily_digest.html', ctx)
    text_body = render_to_string('ml/email/daily_digest.txt', ctx)

    sent = 0
    for user in recipients:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            to=[user.email],
        )
        msg.attach_alternative(html_body, 'text/html')
        try:
            msg.send(fail_silently=False)
            sent += 1
        except Exception:
            # Don't let a single bad address break the whole batch.
            continue
    return sent
