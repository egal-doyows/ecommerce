from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import render

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from menu.models import InventoryItem, Order, OrderItem, RestaurantSettings, Shift
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


# ── #1 Z-Report (End-of-Shift Close) ───────────────────────────────────

def _is_manager(user):
    return user.is_superuser or user.groups.filter(name='Manager').exists()


@login_required(login_url='my-login')
def z_report_list(request):
    """
    Pick a shift to z-report. Managers see all shifts; cashiers see their own.
    """
    shifts = Shift.objects.select_related('waiter').order_by('-started_at')
    if not _is_manager(request.user):
        shifts = shifts.filter(waiter=request.user)
    return render(request, 'reports/z_report_list.html', {
        'shifts': shifts[:50],
        'is_manager': _is_manager(request.user),
    })


def _classify_order(order):
    """
    Map an order onto a loss-prevention category.

    Priority: comp > discount > refund > void > sale.
    A cancelled order is a 'refund' if it had been paid (payment_method set),
    otherwise a 'void'.
    """
    if order.is_comp:
        return 'comp'
    if order.status == 'cancelled':
        return 'refund' if order.payment_method else 'void'
    if order.discount_amount and order.discount_amount > 0:
        return 'discount'
    return 'sale'


@login_required(login_url='my-login')
def z_report_detail(request, shift_id):
    shift = get_object_or_404(Shift.objects.select_related('waiter'), pk=shift_id)
    if not _is_manager(request.user) and shift.waiter_id != request.user.id:
        return redirect('admin-dashboard')

    orders = list(
        shift.orders.prefetch_related('items')
        .order_by('created_at')
    )

    # Categorise each order, then compute totals.
    categorised = {'sale': [], 'void': [], 'refund': [], 'discount': [], 'comp': []}
    for o in orders:
        categorised[_classify_order(o)].append(o)

    paid_sales = [o for o in categorised['sale']]
    gross_sales = sum((o.get_total() for o in paid_sales), Decimal('0'))
    txn_count = len(paid_sales)
    avg_ticket = (gross_sales / txn_count) if txn_count else Decimal('0')

    # Payment method breakdown across paid sales.
    pm_breakdown = {pm: {'count': 0, 'amount': Decimal('0')} for pm, _ in Order.PAYMENT_CHOICES}
    for o in paid_sales:
        if o.payment_method in pm_breakdown:
            pm_breakdown[o.payment_method]['count'] += 1
            pm_breakdown[o.payment_method]['amount'] += o.get_total()

    def _category_total(orders_in_cat, use_discount=False):
        return sum(
            ((o.discount_amount if use_discount else o.get_total()) for o in orders_in_cat),
            Decimal('0'),
        )

    voids = {'count': len(categorised['void']), 'amount': _category_total(categorised['void'])}
    refunds = {'count': len(categorised['refund']), 'amount': _category_total(categorised['refund'])}
    discounts = {
        'count': len(categorised['discount']),
        'amount': _category_total(categorised['discount'], use_discount=True),
    }
    comps = {'count': len(categorised['comp']), 'amount': _category_total(categorised['comp'])}

    cash_sales = pm_breakdown['cash']['amount']
    cash_refunds = sum(
        (o.get_total() for o in categorised['refund'] if o.payment_method == 'cash'),
        Decimal('0'),
    )
    expected_cash = (shift.starting_cash or Decimal('0')) + cash_sales - cash_refunds

    counted_cash = shift.counted_cash
    variance = (counted_cash - expected_cash) if counted_cash is not None else None

    # Top 5 items sold during shift (paid sales only).
    item_totals = {}
    for o in paid_sales:
        for oi in o.items.all():
            key = oi.menu_item_id
            d = item_totals.setdefault(key, {'name': oi.menu_item.title, 'qty': 0, 'revenue': Decimal('0')})
            d['qty'] += oi.quantity
            d['revenue'] += oi.get_subtotal()
    top_items = sorted(item_totals.values(), key=lambda x: x['qty'], reverse=True)[:5]

    duration = None
    if shift.ended_at:
        delta = shift.ended_at - shift.started_at
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        duration = f"{hours}h {minutes}m"

    if request.GET.get('format') == 'csv':
        rows = [
            ['Section', 'Metric', 'Value'],
            ['Shift', 'Cashier', shift.waiter.username if shift.waiter else ''],
            ['Shift', 'Opened', shift.started_at.isoformat()],
            ['Shift', 'Closed', shift.ended_at.isoformat() if shift.ended_at else ''],
            ['Shift', 'Opening float', shift.starting_cash],
            ['Sales', 'Gross sales', gross_sales],
            ['Sales', 'Transactions', txn_count],
            ['Sales', 'Average ticket', avg_ticket],
        ]
        for pm, data in pm_breakdown.items():
            rows.append(['Payments', dict(Order.PAYMENT_CHOICES).get(pm, pm),
                         f'{data["count"]} / {data["amount"]}'])
        rows += [
            ['Loss prevention', 'Voids', f'{voids["count"]} / {voids["amount"]}'],
            ['Loss prevention', 'Refunds', f'{refunds["count"]} / {refunds["amount"]}'],
            ['Loss prevention', 'Discounts', f'{discounts["count"]} / {discounts["amount"]}'],
            ['Loss prevention', 'Comps', f'{comps["count"]} / {comps["amount"]}'],
            ['Cash', 'Expected', expected_cash],
            ['Cash', 'Counted', counted_cash if counted_cash is not None else ''],
            ['Cash', 'Variance', variance if variance is not None else ''],
        ]
        return csv_response(
            f'z_report_shift_{shift.id}.csv',
            ['Section', 'Metric', 'Value'],
            rows[1:],
        )

    return render(request, 'reports/z_report_detail.html', {
        'shift': shift,
        'duration': duration,
        'gross_sales': gross_sales,
        'txn_count': txn_count,
        'avg_ticket': avg_ticket,
        'pm_breakdown': [
            {
                'method': dict(Order.PAYMENT_CHOICES).get(pm, pm),
                'count': data['count'],
                'amount': data['amount'],
            }
            for pm, data in pm_breakdown.items()
        ],
        'voids': voids,
        'refunds': refunds,
        'discounts': discounts,
        'comps': comps,
        'expected_cash': expected_cash,
        'counted_cash': counted_cash,
        'variance': variance,
        'top_items': top_items,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── #2 Daily Sales Summary ─────────────────────────────────────────────

def _daily_sales_for(date):
    """Aggregate sales metrics for a single calendar date."""
    paid_orders = list(
        Order.objects
        .filter(status='paid', is_comp=False, created_at__date=date)
        .prefetch_related('items__menu_item__category')
    )
    cancelled = Order.objects.filter(status='cancelled', created_at__date=date)
    comped = Order.objects.filter(status='paid', is_comp=True, created_at__date=date)

    revenue = sum((o.get_total() for o in paid_orders), Decimal('0'))
    txn_count = len(paid_orders)
    avg_ticket = (revenue / txn_count) if txn_count else Decimal('0')

    # Payment-method split.
    pm_totals = {pm: Decimal('0') for pm, _ in Order.PAYMENT_CHOICES}
    pm_counts = {pm: 0 for pm, _ in Order.PAYMENT_CHOICES}
    for o in paid_orders:
        if o.payment_method in pm_totals:
            pm_totals[o.payment_method] += o.get_total()
            pm_counts[o.payment_method] += 1

    # By hour.
    hourly = {h: {'count': 0, 'revenue': Decimal('0')} for h in range(24)}
    for o in paid_orders:
        local_hour = timezone.localtime(o.created_at).hour
        hourly[local_hour]['count'] += 1
        hourly[local_hour]['revenue'] += o.get_total()

    # Top items by qty and by revenue.
    item_totals = {}
    cat_totals = {}
    for o in paid_orders:
        for oi in o.items.all():
            mi = oi.menu_item
            d = item_totals.setdefault(mi.id, {'name': mi.title, 'qty': 0, 'revenue': Decimal('0')})
            d['qty'] += oi.quantity
            d['revenue'] += oi.get_subtotal()
            cname = mi.category.name if mi.category else 'Uncategorised'
            cat_totals[cname] = cat_totals.get(cname, Decimal('0')) + oi.get_subtotal()
    top_qty = sorted(item_totals.values(), key=lambda x: x['qty'], reverse=True)[:10]
    top_rev = sorted(item_totals.values(), key=lambda x: x['revenue'], reverse=True)[:10]
    by_category = sorted(
        ({'name': k, 'revenue': v} for k, v in cat_totals.items()),
        key=lambda x: x['revenue'], reverse=True,
    )

    # By waiter.
    waiter_totals = {}
    for o in paid_orders:
        if not o.waiter_id:
            continue
        w = waiter_totals.setdefault(o.waiter_id, {
            'username': o.waiter.username, 'count': 0, 'revenue': Decimal('0'),
        })
        w['count'] += 1
        w['revenue'] += o.get_total()
    for w in waiter_totals.values():
        w['avg_ticket'] = (w['revenue'] / w['count']) if w['count'] else Decimal('0')
    by_waiter = sorted(waiter_totals.values(), key=lambda x: x['revenue'], reverse=True)

    return {
        'revenue': revenue,
        'txn_count': txn_count,
        'avg_ticket': avg_ticket,
        'pm_totals': pm_totals,
        'pm_counts': pm_counts,
        'hourly': [
            {'hour': h, 'count': hourly[h]['count'], 'revenue': hourly[h]['revenue']}
            for h in range(24)
        ],
        'top_qty': top_qty,
        'top_rev': top_rev,
        'by_category': by_category,
        'by_waiter': by_waiter,
        'voids_count': cancelled.filter(payment_method='').count(),
        'refunds_count': cancelled.exclude(payment_method='').count(),
        'comps_count': comped.count(),
    }


@manager_required
def daily_sales(request):
    from django.utils import timezone

    target = timezone.localdate() - timedelta(days=1)
    date_param = request.GET.get('date')
    if date_param:
        try:
            target = timezone.datetime.strptime(date_param, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

    today_data = _daily_sales_for(target)
    last_week = target - timedelta(days=7)
    last_week_data = _daily_sales_for(last_week)

    revenue_change = _pct_change(today_data['revenue'], last_week_data['revenue'])

    # Payment-method percentages — guarded against zero revenue.
    pm_pct = {}
    if today_data['revenue']:
        for pm, total in today_data['pm_totals'].items():
            pm_pct[pm] = (total / today_data['revenue'] * 100)
    else:
        pm_pct = {pm: Decimal('0') for pm in today_data['pm_totals']}

    if request.GET.get('format') == 'csv':
        rows = [
            ['Revenue', today_data['revenue']],
            ['Transactions', today_data['txn_count']],
            ['Average ticket', today_data['avg_ticket']],
            ['Voids', today_data['voids_count']],
            ['Refunds', today_data['refunds_count']],
            ['Comps', today_data['comps_count']],
        ]
        for pm, total in today_data['pm_totals'].items():
            rows.append([f'Payments — {dict(Order.PAYMENT_CHOICES).get(pm, pm)}', total])
        return csv_response(
            f'daily_sales_{target.isoformat()}.csv',
            ['Metric', 'Value'], rows,
        )

    pm_rows = [
        {
            'method': dict(Order.PAYMENT_CHOICES).get(pm, pm),
            'amount': today_data['pm_totals'][pm],
            'count': today_data['pm_counts'][pm],
            'pct': pm_pct[pm],
        }
        for pm, _ in Order.PAYMENT_CHOICES
    ]

    return render(request, 'reports/daily_sales.html', {
        'date': target,
        'last_week': last_week,
        'today': today_data,
        'last_week_data': last_week_data,
        'revenue_change': revenue_change,
        'pm_rows': pm_rows,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── #3 Voids, Refunds & Discounts Log ──────────────────────────────────

@manager_required
def voids_log(request):
    from django.contrib.auth.models import User as AuthUser
    from django.db.models import Q

    start, end, preset = parse_date_range(request)

    # Pull every order with any loss-prevention event.
    qs = (
        Order.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
        .filter(
            Q(status='cancelled') | Q(is_comp=True) | Q(discount_amount__gt=0)
        )
        .select_related('waiter', 'authorized_by')
        .prefetch_related('items__menu_item')
        .order_by('-created_at')
    )

    type_filter = request.GET.get('type', '')
    waiter_filter = request.GET.get('waiter', '')
    auth_filter = request.GET.get('authorizer', '')

    rows = []
    counts = {'void': 0, 'refund': 0, 'discount': 0, 'comp': 0}
    amounts = {'void': Decimal('0'), 'refund': Decimal('0'), 'discount': Decimal('0'), 'comp': Decimal('0')}
    waiter_event_counts = {}  # waiter_id → number of events in period

    for o in qs:
        kind = _classify_order(o)
        if kind == 'sale':
            continue
        if type_filter and kind != type_filter:
            continue
        if waiter_filter and str(o.waiter_id) != waiter_filter:
            continue
        if auth_filter and str(o.authorized_by_id) != auth_filter:
            continue

        if kind == 'discount':
            amount = o.discount_amount or Decimal('0')
        else:
            amount = o.get_total()

        items = ', '.join(f'{oi.quantity}× {oi.menu_item.title}' for oi in o.items.all())

        rows.append({
            'timestamp': o.created_at,
            'order_id': o.id,
            'type': kind,
            'items': items or '—',
            'amount': amount,
            'waiter': o.waiter,
            'authorized_by': o.authorized_by,
            'reason': o.authorization_reason,
        })

        # Tallies (computed against the un-filtered set in the period would be misleading;
        # we tally over the displayed set so the summary matches the visible rows).
        counts[kind] += 1
        amounts[kind] += amount
        if o.waiter_id:
            waiter_event_counts[o.waiter_id] = waiter_event_counts.get(o.waiter_id, 0) + 1

    high_volume_waiters = {wid for wid, n in waiter_event_counts.items() if n > 3}
    for r in rows:
        r['flag_pattern'] = r['waiter'] and r['waiter'].id in high_volume_waiters

    if request.GET.get('format') == 'csv':
        csv_rows = [
            [
                r['timestamp'].isoformat(),
                r['order_id'],
                r['type'],
                r['items'],
                r['amount'],
                r['waiter'].username if r['waiter'] else '',
                r['authorized_by'].username if r['authorized_by'] else '',
                r['reason'],
            ]
            for r in rows
        ]
        return csv_response(
            f'voids_log_{start.isoformat()}_to_{end.isoformat()}.csv',
            ['timestamp', 'order_id', 'type', 'items', 'amount', 'waiter', 'authorized_by', 'reason'],
            csv_rows,
        )

    waiters = AuthUser.objects.filter(orders__isnull=False).distinct().order_by('username')
    authorizers = AuthUser.objects.filter(authorised_orders__isnull=False).distinct().order_by('username')

    return render(request, 'reports/voids_log.html', {
        'start': start, 'end': end, 'preset': preset,
        'rows': rows,
        'counts': counts,
        'amounts': amounts,
        'type_filter': type_filter,
        'waiter_filter': waiter_filter,
        'auth_filter': auth_filter,
        'waiters': waiters,
        'authorizers': authorizers,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })
