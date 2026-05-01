from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import render

from menu.models import InventoryItem, Order, RestaurantSettings
from waste.models import WasteItem
from expenses.models import Expense
from staff_compensation.models import PaymentRecord
from debtor.models import Debtor, DebtorTransaction

from .utils import manager_required, parse_date_range, csv_response, superuser_only


@manager_required
def reports_index(request):
    """Landing page for the reports module."""
    return render(request, 'reports/index.html')


# ── #5 Profit & Loss ───────────────────────────────────────────────────

def _decimal_sum(qs, expr):
    """Sum an expression on a queryset, returning Decimal('0') if empty."""
    return qs.aggregate(
        total=Coalesce(Sum(expr), Decimal('0'), output_field=DecimalField()),
    )['total']


def _pl_for_period(start, end):
    """Compute P&L numbers for the inclusive [start, end] date range."""
    paid_orders = Order.objects.filter(
        status='paid',
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    revenue = _decimal_sum(paid_orders, F('items__unit_price') * F('items__quantity'))
    cogs = _decimal_sum(paid_orders, F('items__unit_cost') * F('items__quantity'))

    waste = _decimal_sum(
        WasteItem.objects.filter(
            waste_log__date__gte=start, waste_log__date__lte=end,
        ),
        F('unit_cost') * F('quantity'),
    )

    expenses_qs = Expense.objects.filter(
        date__gte=start, date__lte=end, status='approved',
    )
    expenses_total = _decimal_sum(expenses_qs, F('amount'))
    expenses_by_cat = list(
        expenses_qs.values('category__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    staff_paid = _decimal_sum(
        PaymentRecord.objects.filter(
            status='paid',
            paid_at__date__gte=start,
            paid_at__date__lte=end,
        ),
        F('amount_paid'),
    )

    gross = revenue - cogs
    op_profit = gross - waste - expenses_total - staff_paid
    gross_margin = (gross / revenue * 100) if revenue else Decimal('0')
    net_margin = (op_profit / revenue * 100) if revenue else Decimal('0')

    return {
        'revenue': revenue,
        'cogs': cogs,
        'gross_profit': gross,
        'gross_margin': gross_margin,
        'waste': waste,
        'expenses_total': expenses_total,
        'expenses_by_cat': expenses_by_cat,
        'staff_paid': staff_paid,
        'op_profit': op_profit,
        'net_margin': net_margin,
    }


def _pct_change(current, previous):
    if not previous:
        return None
    return (current - previous) / previous * 100


@manager_required
def profit_loss(request):
    start, end, preset = parse_date_range(request)
    current = _pl_for_period(start, end)

    span = end - start
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - span
    previous = _pl_for_period(prev_start, prev_end)

    deltas = {k: _pct_change(current[k], previous[k]) for k in (
        'revenue', 'cogs', 'gross_profit', 'waste',
        'expenses_total', 'staff_paid', 'op_profit',
    )}

    if request.GET.get('format') == 'csv':
        rows = [
            ['Revenue', current['revenue'], previous['revenue']],
            ['COGS', current['cogs'], previous['cogs']],
            ['Gross Profit', current['gross_profit'], previous['gross_profit']],
            ['Waste', current['waste'], previous['waste']],
            ['Operating Expenses', current['expenses_total'], previous['expenses_total']],
            ['Staff Compensation', current['staff_paid'], previous['staff_paid']],
            ['Operating Profit', current['op_profit'], previous['op_profit']],
        ]
        return csv_response(
            f'profit_loss_{start.isoformat()}_to_{end.isoformat()}.csv',
            ['Item', f'{start} to {end}', f'{prev_start} to {prev_end}'],
            rows,
        )

    return render(request, 'reports/profit_loss.html', {
        'start': start, 'end': end, 'preset': preset,
        'prev_start': prev_start, 'prev_end': prev_end,
        'current': current,
        'previous': previous,
        'deltas': deltas,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── #6 Stock On Hand & Valuation ───────────────────────────────────────

@manager_required
def stock_on_hand(request):
    """Snapshot of current inventory + valuation. Not date-ranged."""
    low_stock_only = request.GET.get('low_stock') == '1'

    items = InventoryItem.objects.select_related('preferred_supplier').order_by('name')
    if low_stock_only:
        items = [i for i in items if i.is_low_stock]
    else:
        items = list(items)

    rows = []
    total_value = Decimal('0')
    for it in items:
        line_value = (it.stock_quantity or Decimal('0')) * (it.buying_price or Decimal('0'))
        total_value += line_value
        rows.append({
            'name': it.name,
            'unit': it.get_unit_display(),
            'stock': it.stock_quantity,
            'cost': it.buying_price,
            'value': line_value,
            'supplier': it.preferred_supplier.name if it.preferred_supplier else '',
            'low_stock': it.is_low_stock,
        })

    if request.GET.get('format') == 'csv':
        # 'counted' column is intentionally blank — for offline physical counting,
        # to be re-uploaded into the variance report.
        header = ['name', 'unit', 'stock', 'counted', 'cost', 'value', 'supplier']
        csv_rows = [
            [r['name'], r['unit'], r['stock'], '', r['cost'], r['value'], r['supplier']]
            for r in rows
        ]
        return csv_response('stock_on_hand.csv', header, csv_rows)

    return render(request, 'reports/stock_on_hand.html', {
        'rows': rows,
        'total_value': total_value,
        'low_stock_only': low_stock_only,
        'item_count': len(rows),
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── #8 Aged Receivables ────────────────────────────────────────────────

def _bucket_age(age_days):
    """Map an age-in-days to one of the four aging buckets."""
    if age_days <= 30:
        return 'b0_30'
    if age_days <= 60:
        return 'b31_60'
    if age_days <= 90:
        return 'b61_90'
    return 'b90_plus'


@manager_required
def aged_receivables(request):
    """Per-debtor outstanding balance bucketed by invoice age."""
    from django.utils import timezone

    as_of = timezone.localdate()
    custom_as_of = request.GET.get('as_of')
    if custom_as_of:
        try:
            as_of = timezone.datetime.strptime(custom_as_of, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

    open_invoices = (
        DebtorTransaction.objects
        .filter(transaction_type='debit')
        .filter(date__lte=as_of)
        .select_related('debtor')
    )

    by_debtor = {}
    for inv in open_invoices:
        outstanding = (inv.amount or Decimal('0')) - (inv.amount_paid or Decimal('0'))
        if outstanding <= 0:
            continue
        age = (as_of - inv.date).days
        bucket = _bucket_age(age)
        d = by_debtor.setdefault(inv.debtor_id, {
            'debtor': inv.debtor,
            'b0_30': Decimal('0'),
            'b31_60': Decimal('0'),
            'b61_90': Decimal('0'),
            'b90_plus': Decimal('0'),
            'total': Decimal('0'),
        })
        d[bucket] += outstanding
        d['total'] += outstanding

    rows = sorted(by_debtor.values(), key=lambda r: r['total'], reverse=True)

    totals = {
        'b0_30': sum((r['b0_30'] for r in rows), Decimal('0')),
        'b31_60': sum((r['b31_60'] for r in rows), Decimal('0')),
        'b61_90': sum((r['b61_90'] for r in rows), Decimal('0')),
        'b90_plus': sum((r['b90_plus'] for r in rows), Decimal('0')),
        'total': sum((r['total'] for r in rows), Decimal('0')),
    }

    if request.GET.get('format') == 'csv':
        header = ['debtor', '0-30', '31-60', '61-90', '90+', 'total']
        csv_rows = [
            [r['debtor'].name, r['b0_30'], r['b31_60'], r['b61_90'], r['b90_plus'], r['total']]
            for r in rows
        ]
        csv_rows.append(['TOTAL', totals['b0_30'], totals['b31_60'], totals['b61_90'], totals['b90_plus'], totals['total']])
        return csv_response(f'aged_receivables_{as_of.isoformat()}.csv', header, csv_rows)

    return render(request, 'reports/aged_receivables.html', {
        'rows': rows,
        'totals': totals,
        'as_of': as_of,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── #10 Audit Trail (Owner-only) ───────────────────────────────────────

ACTION_LABELS = {0: 'create', 1: 'update', 2: 'delete'}
ACTION_BY_NAME = {v: k for k, v in ACTION_LABELS.items()}


@superuser_only
def audit_trail(request):
    from auditlog.models import LogEntry
    from django.contrib.auth.models import User as AuthUser
    from django.contrib.contenttypes.models import ContentType
    from django.core.paginator import Paginator

    start, end, preset = parse_date_range(request)

    qs = (
        LogEntry.objects
        .filter(timestamp__date__gte=start, timestamp__date__lte=end)
        .select_related('actor', 'content_type')
        .order_by('-timestamp')
    )

    action_filter = request.GET.get('action', '')
    if action_filter in ACTION_BY_NAME:
        qs = qs.filter(action=ACTION_BY_NAME[action_filter])

    user_filter = request.GET.get('user', '')
    if user_filter:
        qs = qs.filter(actor_id=user_filter)

    model_filter = request.GET.get('model', '')
    if model_filter:
        qs = qs.filter(content_type__model=model_filter)

    if request.GET.get('format') == 'csv':
        header = ['timestamp', 'user', 'action', 'target', 'object_id', 'changes', 'ip']
        rows = (
            [
                e.timestamp.isoformat(),
                e.actor.username if e.actor else '',
                ACTION_LABELS.get(e.action, str(e.action)),
                f'{e.content_type.app_label}.{e.content_type.model}' if e.content_type else '',
                e.object_pk,
                e.changes,
                e.remote_addr or '',
            ]
            for e in qs.iterator(chunk_size=500)
        )
        return csv_response(
            f'audit_trail_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, rows,
        )

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Decorate entries with a friendly action label.
    entries = [
        {
            'timestamp': e.timestamp,
            'actor': e.actor,
            'action': ACTION_LABELS.get(e.action, str(e.action)),
            'content_type': e.content_type,
            'object_pk': e.object_pk,
            'object_repr': e.object_repr,
            'changes': e.changes,
            'remote_addr': e.remote_addr,
        }
        for e in page_obj.object_list
    ]

    # Filter dropdown data — only models that actually appear in the log + active users.
    distinct_models = (
        LogEntry.objects
        .values_list('content_type__app_label', 'content_type__model')
        .distinct()
        .order_by('content_type__app_label', 'content_type__model')
    )
    models = [
        {'value': m, 'label': f'{a}.{m}'}
        for a, m in distinct_models if m
    ]
    users = AuthUser.objects.filter(logentry__isnull=False).distinct().order_by('username')

    return render(request, 'reports/audit_trail.html', {
        'start': start, 'end': end, 'preset': preset,
        'entries': entries,
        'page_obj': page_obj,
        'action_filter': action_filter,
        'user_filter': user_filter,
        'model_filter': model_filter,
        'action_choices': list(ACTION_LABELS.values()),
        'models': models,
        'users': users,
    })
