"""
Shared infrastructure for report views.

- `manager_required`: gate to Manager-or-superuser.
- `parse_date_range`: pulls start/end/preset off the request.
- `csv_response`: serialise an iterable of rows to a downloadable CSV.
"""

import csv
from datetime import datetime, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone


def manager_required(view_func):
    """Manager group + superusers only. Redirect to admin dashboard otherwise."""
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not (u.is_superuser or u.groups.filter(name='Manager').exists()):
            messages.error(request, 'Reports are restricted to managers.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def superuser_only(view_func):
    """Owner / superuser only — for sensitive reports like the audit trail."""
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'This report is restricted to the owner.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def parse_date_range(request):
    """
    Resolve the requested period.

    Reads `?preset=today|yesterday|week|month|custom` plus `?start=YYYY-MM-DD`
    and `?end=YYYY-MM-DD` for custom ranges. Returns (start_date, end_date,
    preset_label). Always returns date objects, inclusive of both endpoints.
    """
    today = timezone.localdate()
    preset = request.GET.get('preset', 'today')
    start_param = request.GET.get('start')
    end_param = request.GET.get('end')

    def _parse(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None

    if preset == 'custom' and (start_param or end_param):
        start = _parse(start_param) or today
        end = _parse(end_param) or today
        if end < start:
            start, end = end, start
        return start, end, 'custom'

    if preset == 'yesterday':
        d = today - timedelta(days=1)
        return d, d, 'yesterday'

    if preset == 'week':
        start = today - timedelta(days=today.weekday())  # Monday
        end = start + timedelta(days=6)
        return start, end, 'week'

    if preset == 'month':
        start = today.replace(day=1)
        if start.month == 12:
            next_first = start.replace(year=start.year + 1, month=1)
        else:
            next_first = start.replace(month=start.month + 1)
        end = next_first - timedelta(days=1)
        return start, end, 'month'

    return today, today, 'today'


def csv_response(filename, header, rows):
    """
    Return an HttpResponse with a CSV body.

    `rows` may be an iterable of lists/tuples, or of dicts (in which case
    `header` is used as both the column order and the keys to look up).
    """
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        if isinstance(row, dict):
            writer.writerow([row.get(col, '') for col in header])
        else:
            writer.writerow(row)
    return response
