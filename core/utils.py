"""Shared utility functions used across multiple apps."""

from datetime import datetime

from django.utils import timezone as tz


def parse_date(value, default=None):
    """Parse a YYYY-MM-DD string into a date, returning *default* on failure."""
    if not value:
        return default
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return default


def parse_date_range(request):
    """Extract ``date_from`` / ``date_to`` from GET params.

    Returns (date_from, date_to, raw_from_str, raw_to_str).
    Defaults to first-of-month → today.
    """
    today = tz.now().date()
    raw_from = request.GET.get('date_from', today.replace(day=1).isoformat())
    raw_to = request.GET.get('date_to', today.isoformat())
    d_from = parse_date(raw_from, default=today.replace(day=1))
    d_to = parse_date(raw_to, default=today)
    return d_from, d_to, raw_from, raw_to


def branch_filter_kwargs(pk, request, is_overall):
    """Build a filter dict that scopes to the user's branch unless they are overall."""
    kwargs = {'pk': pk}
    if not is_overall:
        kwargs['branch'] = request.branch
    return kwargs
