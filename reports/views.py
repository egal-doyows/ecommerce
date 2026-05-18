from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import render

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from administration.models import Account, Transaction
from menu.models import InventoryItem, MenuItem, Order, OrderItem, RestaurantSettings, Shift, StockAdjustment
from waste.models import WasteItem, WasteLog
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
    """True for users who may inspect every shift on the z-report.

    Includes Supervisors so floor leads can review their team's shifts; the
    name is kept for minimal diff against existing callers, but the gate is
    really 'can view all shifts'."""
    return user.is_superuser or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()


@login_required(login_url='my-login')
def z_report_list(request):
    """
    Pick a shift to z-report. Managers/Supervisors see all shifts; everyone
    else sees their own. Annotates each row with the headline numbers
    (opening, counted, gross sales, cash sales, variance) so supervisors
    can scan without opening each detail page.
    """
    shifts = (
        Shift.objects.select_related('waiter')
        .prefetch_related('orders')
        .order_by('-started_at')[:50]
    )
    if not _is_manager(request.user):
        shifts = [s for s in shifts if s.waiter_id == request.user.id]

    payment_methods = list(Order.PAYMENT_CHOICES)  # [(key, label), ...]
    rows = []
    for s in shifts:
        paid = [o for o in s.orders.all() if o.status == 'paid']
        gross = sum((o.get_total() for o in paid), Decimal('0'))
        pm_totals = {pm: Decimal('0') for pm, _ in payment_methods}
        for o in paid:
            if o.payment_method in pm_totals:
                pm_totals[o.payment_method] += o.get_total()
        cash_refunds = sum(
            (o.get_total() for o in s.orders.all()
             if o.status == 'cancelled' and o.payment_method == 'cash'),
            Decimal('0'),
        )
        expected = (s.starting_cash or Decimal('0')) + pm_totals['cash'] - cash_refunds
        variance = (s.counted_cash - expected) if s.counted_cash is not None else None
        rows.append({
            'shift': s,
            'gross': gross,
            'txn_count': len(paid),
            'opening': s.starting_cash or Decimal('0'),
            'counted': s.counted_cash,
            'expected': expected,
            'variance': variance,
            'pm_amounts': [pm_totals[pm] for pm, _ in payment_methods],
        })

    return render(request, 'reports/z_report_list.html', {
        'rows': rows,
        'payment_methods': payment_methods,
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
def shift_record_count(request, shift_id):
    """Supervisor / manager / superuser records the till count after the
    server clocks out. Server is intentionally excluded — separation of
    duties: whoever counts the drawer is not the same person who ran it.

    Concurrency model: SELECT FOR UPDATE on the shift row plus re-checking
    every precondition inside the lock makes the operation idempotent and
    safe against:
      - Two supervisors recording simultaneously (one wins, the other gets
        a clear 'already counted by X' warning).
      - The same supervisor double-clicking the form.
      - A new active order being attached to the shift between guard check
        and save.
    """
    if request.method != 'POST':
        return redirect('reports-z-report-detail', shift_id=shift_id)

    if not _is_manager(request.user):
        messages.error(request, 'Only supervisors and managers can record till counts.')
        return redirect('reports-z-report-detail', shift_id=shift_id)

    # Validate the input before opening a transaction — no point locking
    # the row to find out the form is empty.
    raw = request.POST.get('counted_cash', '').strip()
    if not raw:
        messages.error(request, 'Enter the counted cash.')
        return redirect('reports-z-report-detail', shift_id=shift_id)
    try:
        counted = Decimal(raw)
        if counted < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        messages.error(request, 'Counted cash must be a number ≥ 0.')
        return redirect('reports-z-report-detail', shift_id=shift_id)

    with transaction.atomic():
        try:
            shift = Shift.objects.select_for_update().get(pk=shift_id)
        except Shift.DoesNotExist:
            raise Http404

        # Re-check every precondition while holding the row lock. The
        # outside-template hint that the form is allowed is just UX; the
        # real authority is here.
        if shift.waiter_id == request.user.id:
            messages.error(request, 'You cannot count your own till — ask another supervisor.')
            return redirect('reports-z-report-detail', shift_id=shift_id)

        if shift.counted_cash is not None:
            who = shift.counted_by.username if shift.counted_by else 'another supervisor'
            when = shift.counted_at.strftime('%H:%M') if shift.counted_at else ''
            messages.warning(
                request,
                f'Shift #{shift.id} was already counted by {who}{" at " + when if when else ""}.',
            )
            return redirect('reports-z-report-detail', shift_id=shift_id)

        unpaid = shift.orders.filter(status='active').count()
        if unpaid:
            messages.error(
                request,
                f'Cannot count the till — this shift still has {unpaid} '
                f'unpaid order{"s" if unpaid != 1 else ""}. Settle or void them first.',
            )
            return redirect('reports-z-report-detail', shift_id=shift_id)

        shift.counted_cash = counted
        shift.counted_by = request.user
        shift.counted_at = timezone.now()
        if shift.ended_at is None:
            shift.ended_at = timezone.now()
        shift.save()

    messages.success(request, f'Counted cash recorded for shift #{shift.id}.')
    return redirect('reports-z-report-detail', shift_id=shift_id)


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
        'unpaid_count': sum(1 for o in orders if o.status == 'active'),
        'can_record_count': (
            _is_manager(request.user)
            and shift.waiter_id != request.user.id
            and shift.counted_cash is None
            and not any(o.status == 'active' for o in orders)
        ),
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


# ── #4 Cash Drawer Reconciliation ──────────────────────────────────────

def _shift_cash_reconciliation(shift):
    """
    Compute expected drawer cash + counted + variance for one shift.

    expected = opening + cash sales − cash refunds
    (Cash deposits and payouts mid-shift are not modelled yet — assumed zero.)
    """
    cash_sales = Decimal('0')
    cash_refunds = Decimal('0')
    for o in shift.orders.all():
        if o.payment_method != 'cash':
            continue
        total = o.get_total()
        if o.status == 'paid' and not o.is_comp:
            cash_sales += total
        elif o.status == 'cancelled':
            cash_refunds += total

    expected = (shift.starting_cash or Decimal('0')) + cash_sales - cash_refunds
    counted = shift.counted_cash
    variance = (counted - expected) if counted is not None else None
    return expected, counted, variance, cash_sales, cash_refunds


@manager_required
def cash_drawer(request):
    cashier_filter = request.GET.get('cashier', '')
    qs = (
        Shift.objects.select_related('waiter')
        .prefetch_related('orders')
        .order_by('-started_at')
    )
    if cashier_filter:
        qs = qs.filter(waiter_id=cashier_filter)
    shifts = list(qs[:30])

    rows = []
    for s in shifts:
        expected, counted, variance, cash_sales, cash_refunds = _shift_cash_reconciliation(s)
        rows.append({
            'shift': s,
            'expected': expected,
            'counted': counted,
            'variance': variance,
            'cash_sales': cash_sales,
            'cash_refunds': cash_refunds,
        })

    # Per-cashier aggregate across the displayed shifts (only counted shifts).
    by_cashier = {}
    for r in rows:
        if r['variance'] is None or not r['shift'].waiter_id:
            continue
        c = by_cashier.setdefault(r['shift'].waiter_id, {
            'username': r['shift'].waiter.username,
            'shift_count': 0,
            'total_variance': Decimal('0'),
            'shorts': 0,
        })
        c['shift_count'] += 1
        c['total_variance'] += r['variance']
        if r['variance'] < 0:
            c['shorts'] += 1
    for c in by_cashier.values():
        c['avg_variance'] = (c['total_variance'] / c['shift_count']) if c['shift_count'] else Decimal('0')
    cashier_summary = sorted(by_cashier.values(), key=lambda x: x['total_variance'])

    if request.GET.get('format') == 'csv':
        csv_rows = [
            [
                r['shift'].id,
                r['shift'].waiter.username if r['shift'].waiter else '',
                r['shift'].started_at.isoformat(),
                r['shift'].ended_at.isoformat() if r['shift'].ended_at else '',
                r['expected'],
                r['counted'] if r['counted'] is not None else '',
                r['variance'] if r['variance'] is not None else '',
            ]
            for r in rows
        ]
        return csv_response(
            'cash_drawer.csv',
            ['shift_id', 'cashier', 'opened', 'closed', 'expected', 'counted', 'variance'],
            csv_rows,
        )

    from django.contrib.auth.models import User as AuthUser
    cashiers = AuthUser.objects.filter(shifts__isnull=False).distinct().order_by('username')

    return render(request, 'reports/cash_drawer.html', {
        'rows': rows,
        'cashier_summary': cashier_summary,
        'cashiers': cashiers,
        'cashier_filter': cashier_filter,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── #7 Stock Variance Report ───────────────────────────────────────────

def _parse_count(raw):
    try:
        return Decimal(str(raw).strip())
    except Exception:
        return None


@manager_required
def stock_variance(request):
    from django.contrib import messages
    from django.db import transaction

    items = list(InventoryItem.objects.all().order_by('name'))
    by_id = {i.id: i for i in items}

    # GET → blank form, system stock pre-filled.
    if request.method != 'POST':
        rows = [
            {'item': i, 'system': i.stock_quantity, 'counted': i.stock_quantity,
             'variance': Decimal('0'), 'variance_value': Decimal('0'),
             'variance_pct': Decimal('0'), 'flagged': False}
            for i in items
        ]
        return render(request, 'reports/stock_variance.html', {
            'rows': rows,
            'phase': 'enter',
            'total_shrinkage': Decimal('0'),
            'currency_symbol': RestaurantSettings.load().currency_symbol,
        })

    # POST — parse the submitted counts.
    counts = {}
    for key, value in request.POST.items():
        if not key.startswith('count_'):
            continue
        try:
            item_id = int(key[len('count_'):])
        except ValueError:
            continue
        if item_id not in by_id:
            continue
        parsed = _parse_count(value)
        if parsed is not None:
            counts[item_id] = parsed

    rows = []
    total_shrinkage = Decimal('0')
    nonzero_variances = []
    for i in items:
        counted = counts.get(i.id, i.stock_quantity)
        variance = counted - (i.stock_quantity or Decimal('0'))
        cost = i.buying_price or Decimal('0')
        variance_value = variance * cost
        if i.stock_quantity:
            variance_pct = (variance / i.stock_quantity * 100)
        else:
            variance_pct = None
        flagged = variance_pct is not None and abs(variance_pct) > 5
        rows.append({
            'item': i,
            'system': i.stock_quantity,
            'counted': counted,
            'variance': variance,
            'variance_value': variance_value,
            'variance_pct': variance_pct,
            'flagged': flagged,
        })
        total_shrinkage += variance_value
        if variance != 0:
            nonzero_variances.append((i, counted, variance))

    confirm = request.POST.get('confirm') == '1'
    posted_count = 0

    if confirm and nonzero_variances:
        with transaction.atomic():
            for i, counted, variance in nonzero_variances:
                StockAdjustment.objects.create(
                    inventory_item=i,
                    qty_delta=variance,
                    reason='Physical count',
                    source='count',
                    created_by=request.user,
                )
                i.stock_quantity = counted
                i.save(update_fields=['stock_quantity'])
                posted_count += 1
        messages.success(
            request,
            f'Posted {posted_count} stock adjustment(s); inventory now matches the count.',
        )
        return render(request, 'reports/stock_variance.html', {
            'rows': [
                {'item': i, 'system': i.stock_quantity, 'counted': i.stock_quantity,
                 'variance': Decimal('0'), 'variance_value': Decimal('0'),
                 'variance_pct': Decimal('0'), 'flagged': False}
                for i in InventoryItem.objects.all().order_by('name')
            ],
            'phase': 'enter',
            'total_shrinkage': Decimal('0'),
            'posted_count': posted_count,
            'currency_symbol': RestaurantSettings.load().currency_symbol,
        })

    return render(request, 'reports/stock_variance.html', {
        'rows': rows,
        'phase': 'preview',
        'total_shrinkage': total_shrinkage,
        'has_variances': bool(nonzero_variances),
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Sales by Channel ───────────────────────────────────────────────────

@manager_required
def sales_by_channel(request):
    """Break paid revenue down by order_type (Dine-in/Takeaway/Delivery)
    and source (POS/Phone/Uber Eats/Glovo/Bolt/Jumia/Other). Useful for
    splitting walk-in vs marketplace revenue and tracking platform fees.
    """
    start, end, preset = parse_date_range(request)

    paid_orders = (
        Order.objects
        .filter(status='paid', created_at__date__gte=start, created_at__date__lte=end)
        .prefetch_related('items')
    )

    type_labels = dict(Order.ORDER_TYPE_CHOICES)
    source_labels = dict(Order.SOURCE_CHOICES)

    by_type = {k: {'count': 0, 'revenue': Decimal('0')} for k, _ in Order.ORDER_TYPE_CHOICES}
    by_source = {k: {'count': 0, 'revenue': Decimal('0')} for k, _ in Order.SOURCE_CHOICES}
    cross = {}

    total_revenue = Decimal('0')
    total_count = 0

    for order in paid_orders:
        revenue = order.get_total()
        total_revenue += revenue
        total_count += 1

        by_type[order.order_type]['count'] += 1
        by_type[order.order_type]['revenue'] += revenue
        by_source[order.source]['count'] += 1
        by_source[order.source]['revenue'] += revenue

        key = (order.order_type, order.source)
        if key not in cross:
            cross[key] = {'count': 0, 'revenue': Decimal('0')}
        cross[key]['count'] += 1
        cross[key]['revenue'] += revenue

    def _pct(amount):
        if not total_revenue:
            return Decimal('0')
        return amount / total_revenue * 100

    type_rows = [
        {
            'key': k,
            'label': type_labels[k],
            'count': v['count'],
            'revenue': v['revenue'],
            'pct': _pct(v['revenue']),
        }
        for k, v in by_type.items() if v['count']
    ]
    type_rows.sort(key=lambda r: r['revenue'], reverse=True)

    source_rows = [
        {
            'key': k,
            'label': source_labels[k],
            'count': v['count'],
            'revenue': v['revenue'],
            'pct': _pct(v['revenue']),
        }
        for k, v in by_source.items() if v['count']
    ]
    source_rows.sort(key=lambda r: r['revenue'], reverse=True)

    cross_rows = [
        {
            'type_label': type_labels[t],
            'source_label': source_labels[s],
            'count': v['count'],
            'revenue': v['revenue'],
            'pct': _pct(v['revenue']),
        }
        for (t, s), v in cross.items()
    ]
    cross_rows.sort(key=lambda r: r['revenue'], reverse=True)

    if request.GET.get('format') == 'csv':
        header = ['Group', 'Label', 'Orders', 'Revenue', '% of revenue']
        rows = []
        for r in type_rows:
            rows.append(['Order type', r['label'], r['count'], r['revenue'], f"{r['pct']:.1f}"])
        for r in source_rows:
            rows.append(['Source', r['label'], r['count'], r['revenue'], f"{r['pct']:.1f}"])
        for r in cross_rows:
            rows.append([
                'Type × Source',
                f"{r['type_label']} · {r['source_label']}",
                r['count'], r['revenue'], f"{r['pct']:.1f}",
            ])
        return csv_response(
            f'sales_by_channel_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, rows,
        )

    return render(request, 'reports/sales_by_channel.html', {
        'start': start, 'end': end, 'preset': preset,
        'type_rows': type_rows,
        'source_rows': source_rows,
        'cross_rows': cross_rows,
        'total_revenue': total_revenue,
        'total_count': total_count,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Channel Margin ─────────────────────────────────────────────────────

@manager_required
def channel_margin(request):
    """
    Revenue by source NET of platform commission fees. Walk-in /
    in-house channels (POS, phone) keep 100% of revenue; marketplaces
    (Uber Eats, Glovo, Bolt, Jumia) lose the commission percentage
    configured on RestaurantSettings.

    True margin = gross revenue − platform fee − COGS. Margin % is
    against gross revenue (the headline customer paid) so it's
    comparable across channels.
    """
    start, end, preset = parse_date_range(request)
    settings = RestaurantSettings.load()
    source_labels = dict(Order.SOURCE_CHOICES)

    commission_map = {
        'ubereats': settings.ubereats_commission_pct,
        'glovo': settings.glovo_commission_pct,
        'bolt': settings.bolt_commission_pct,
        'jumia': settings.jumia_commission_pct,
        'pos': Decimal('0'),
        'phone': Decimal('0'),
        'other': Decimal('0'),
    }

    qs = OrderItem.objects.filter(
        order__status='paid', order__is_comp=False,
        order__created_at__date__gte=start,
        order__created_at__date__lte=end,
    )

    aggregates = (
        qs.values('order__source')
        .annotate(
            qty_sold=Sum('quantity'),
            revenue=Coalesce(
                Sum(F('unit_price') * F('quantity'), output_field=DecimalField()),
                Decimal('0'), output_field=DecimalField(),
            ),
            cost=Coalesce(
                Sum(F('unit_cost') * F('quantity'), output_field=DecimalField()),
                Decimal('0'), output_field=DecimalField(),
            ),
        )
    )

    rows = []
    total_revenue = Decimal('0')
    total_cost = Decimal('0')
    total_fee = Decimal('0')
    total_qty = 0
    for a in aggregates:
        source = a['order__source']
        revenue = a['revenue'] or Decimal('0')
        cost = a['cost'] or Decimal('0')
        commission_pct = commission_map.get(source, Decimal('0'))
        fee = (revenue * commission_pct / Decimal('100')).quantize(Decimal('0.01'))
        net_revenue = revenue - fee
        margin = net_revenue - cost
        gross_margin = revenue - cost
        margin_pct = (margin / revenue * 100) if revenue else Decimal('0')
        gross_margin_pct = (gross_margin / revenue * 100) if revenue else Decimal('0')

        rows.append({
            'source': source,
            'label': source_labels.get(source, source),
            'qty_sold': a['qty_sold'],
            'commission_pct': commission_pct,
            'revenue': revenue,
            'fee': fee,
            'net_revenue': net_revenue,
            'cost': cost,
            'gross_margin': gross_margin,
            'gross_margin_pct': gross_margin_pct,
            'margin': margin,
            'margin_pct': margin_pct,
        })
        total_revenue += revenue
        total_cost += cost
        total_fee += fee
        total_qty += a['qty_sold']

    # % of revenue mix.
    for r in rows:
        r['mix_pct'] = (r['revenue'] / total_revenue * 100) if total_revenue else Decimal('0')

    total_net_revenue = total_revenue - total_fee
    total_margin = total_net_revenue - total_cost
    total_margin_pct = (total_margin / total_revenue * 100) if total_revenue else Decimal('0')

    rows.sort(key=lambda r: r['margin'], reverse=True)

    if request.GET.get('format') == 'csv':
        header = [
            'source', 'qty_sold', 'gross_revenue', 'commission_pct',
            'platform_fee', 'net_revenue', 'cost', 'true_margin',
            'true_margin_pct', 'mix_pct',
        ]
        csv_rows = [
            [
                r['label'], r['qty_sold'], r['revenue'],
                f"{r['commission_pct']:.2f}", r['fee'], r['net_revenue'],
                r['cost'], r['margin'],
                f"{r['margin_pct']:.1f}", f"{r['mix_pct']:.1f}",
            ]
            for r in rows
        ]
        return csv_response(
            f'channel_margin_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, csv_rows,
        )

    return render(request, 'reports/channel_margin.html', {
        'start': start, 'end': end, 'preset': preset,
        'rows': rows,
        'total_qty': total_qty,
        'total_revenue': total_revenue,
        'total_fee': total_fee,
        'total_net_revenue': total_net_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'total_margin_pct': total_margin_pct,
        'currency_symbol': settings.currency_symbol,
    })


# ── Recipe Cost Drift ──────────────────────────────────────────────────

@manager_required
def recipe_cost_drift(request):
    """
    Compare each menu item's CURRENT cost (computed from today's
    inventory buying_prices) against the AVERAGE cost we recorded when
    the item was actually sold N days ago (frozen OrderItem.unit_cost).

    Cost going up faster than price = margin quietly eroded. The report
    surfaces items where the cost has drifted enough that the menu
    price should probably be re-examined.

    Only items with at least one paid sale in the historical window are
    shown — without sales there's no historical baseline to compare to.
    """
    from django.utils import timezone

    try:
        lookback_days = int(request.GET.get('lookback', '90'))
    except (ValueError, TypeError):
        lookback_days = 90
    lookback_days = max(7, min(lookback_days, 365))

    today = timezone.localdate()
    window_end = today
    window_start = today - timedelta(days=lookback_days)

    # Historical avg cost per menu item, weighted by quantity sold.
    historical = (
        OrderItem.objects
        .filter(
            order__status='paid', order__is_comp=False,
            order__created_at__date__gte=window_start,
            order__created_at__date__lte=window_end,
        )
        .values('menu_item_id')
        .annotate(
            qty=Sum('quantity'),
            cost_sum=Sum(F('unit_cost') * F('quantity'), output_field=DecimalField()),
        )
    )

    menu_items = (
        MenuItem.objects
        .select_related('category', 'inventory_item')
        .prefetch_related('recipe_items__inventory_item')
    )
    by_id = {mi.id: mi for mi in menu_items}

    rows = []
    for h in historical:
        mi = by_id.get(h['menu_item_id'])
        if mi is None:
            continue
        qty = Decimal(str(h['qty'] or 0))
        if qty <= 0:
            continue
        cost_sum = h['cost_sum'] or Decimal('0')
        historical_avg_cost = cost_sum / qty
        current_cost = mi.current_unit_cost()
        delta = current_cost - historical_avg_cost
        if historical_avg_cost > 0:
            drift_pct = (delta / historical_avg_cost * 100)
        else:
            drift_pct = None

        price = mi.price or Decimal('0')
        historical_margin = price - historical_avg_cost
        current_margin = price - current_cost
        historical_margin_pct = (historical_margin / price * 100) if price else Decimal('0')
        current_margin_pct = (current_margin / price * 100) if price else Decimal('0')
        margin_delta_pct = current_margin_pct - historical_margin_pct

        rows.append({
            'menu_item': mi,
            'title': mi.title,
            'category': mi.category.name if mi.category else 'Uncategorised',
            'qty_sold': qty,
            'price': price,
            'historical_cost': historical_avg_cost,
            'current_cost': current_cost,
            'cost_delta': delta,
            'drift_pct': drift_pct,
            'historical_margin_pct': historical_margin_pct,
            'current_margin_pct': current_margin_pct,
            'margin_delta_pct': margin_delta_pct,
            'flagged': drift_pct is not None and drift_pct >= 10,
        })

    sort_key = request.GET.get('sort', 'drift')
    sort_dir = request.GET.get('dir', 'desc')
    reverse = sort_dir != 'asc'
    sort_field = {
        'title': 'title',
        'qty': 'qty_sold',
        'historical_cost': 'historical_cost',
        'current_cost': 'current_cost',
        'drift': 'drift_pct',
        'margin_delta': 'margin_delta_pct',
    }.get(sort_key, 'drift_pct')

    def _key(r):
        v = r[sort_field]
        return (1 if v is None else 0, v if v is not None else Decimal('0'))
    rows.sort(key=_key, reverse=reverse)

    if request.GET.get('format') == 'csv':
        header = [
            'category', 'item', 'qty_sold', 'price',
            'historical_cost', 'current_cost', 'cost_drift_pct',
            'historical_margin_pct', 'current_margin_pct', 'margin_delta_pct',
        ]
        csv_rows = [
            [
                r['category'], r['title'], r['qty_sold'], r['price'],
                r['historical_cost'], r['current_cost'],
                f"{r['drift_pct']:.1f}" if r['drift_pct'] is not None else '',
                f"{r['historical_margin_pct']:.1f}",
                f"{r['current_margin_pct']:.1f}",
                f"{r['margin_delta_pct']:.1f}",
            ]
            for r in rows
        ]
        return csv_response(f'recipe_cost_drift_{lookback_days}d.csv', header, csv_rows)

    return render(request, 'reports/recipe_cost_drift.html', {
        'rows': rows,
        'lookback_days': lookback_days,
        'window_start': window_start,
        'window_end': window_end,
        'flagged_count': sum(1 for r in rows if r['flagged']),
        'sort_key': sort_key,
        'sort_dir': sort_dir,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Slow Movers / Dead Stock ───────────────────────────────────────────

@manager_required
def slow_movers(request):
    """
    Inventory items with low or zero movement in the period, ranked by
    current stock value (capital tied up that isn't earning).

    Movement = direct sales + recipe consumption + waste.
    Days of cover = stock_quantity / (movement / days_in_period). 'inf'
    means no movement at all in the period.
    """
    from menu.models import Recipe

    start, end, preset = parse_date_range(request)
    days_in_period = (end - start).days + 1
    threshold_days = request.GET.get('threshold', '60')
    try:
        threshold_days = int(threshold_days)
    except (ValueError, TypeError):
        threshold_days = 60

    # Direct-sale consumption: OrderItem.quantity per inventory_item via menu_item.
    direct = (
        OrderItem.objects
        .filter(
            order__status='paid', order__is_comp=False,
            order__created_at__date__gte=start,
            order__created_at__date__lte=end,
            menu_item__inventory_item__isnull=False,
        )
        .values('menu_item__inventory_item_id')
        .annotate(qty=Sum('quantity'))
    )
    movement = {d['menu_item__inventory_item_id']: Decimal(str(d['qty'] or 0)) for d in direct}

    # Recipe consumption: for each OrderItem, walk recipe rows.
    recipe_items = (
        OrderItem.objects
        .filter(
            order__status='paid', order__is_comp=False,
            order__created_at__date__gte=start,
            order__created_at__date__lte=end,
            menu_item__recipe_items__isnull=False,
        )
        .values('menu_item_id')
        .annotate(qty=Sum('quantity'))
    )
    menu_qtys = {r['menu_item_id']: Decimal(str(r['qty'] or 0)) for r in recipe_items}
    if menu_qtys:
        recipes = Recipe.objects.filter(menu_item_id__in=menu_qtys.keys())
        for r in recipes:
            used = menu_qtys[r.menu_item_id] * r.quantity_required
            movement[r.inventory_item_id] = movement.get(r.inventory_item_id, Decimal('0')) + used

    # Waste in the period also counts as movement.
    waste = (
        WasteItem.objects
        .filter(waste_log__date__gte=start, waste_log__date__lte=end)
        .values('inventory_item_id')
        .annotate(qty=Sum('quantity'))
    )
    for w in waste:
        movement[w['inventory_item_id']] = movement.get(w['inventory_item_id'], Decimal('0')) + w['qty']

    items = InventoryItem.objects.select_related('preferred_supplier').all()
    rows = []
    flagged_value = Decimal('0')
    for it in items:
        mvt = movement.get(it.id, Decimal('0'))
        stock_value = (it.stock_quantity or Decimal('0')) * (it.buying_price or Decimal('0'))
        if mvt > 0 and days_in_period > 0:
            daily_burn = mvt / Decimal(str(days_in_period))
            days_cover = (it.stock_quantity / daily_burn) if daily_burn else None
        else:
            days_cover = None  # no movement → infinite cover

        # "Slow" = no movement OR cover exceeds the threshold.
        is_slow = (mvt == 0) or (days_cover is not None and days_cover > threshold_days)
        if not is_slow:
            continue
        rows.append({
            'item': it,
            'stock': it.stock_quantity,
            'unit': it.get_unit_display(),
            'cost': it.buying_price,
            'stock_value': stock_value,
            'movement': mvt,
            'days_cover': days_cover,
            'supplier': it.preferred_supplier.name if it.preferred_supplier else '',
            'no_movement': mvt == 0,
        })
        flagged_value += stock_value

    rows.sort(key=lambda r: r['stock_value'], reverse=True)

    if request.GET.get('format') == 'csv':
        header = ['item', 'unit', 'stock', 'cost', 'stock_value',
                  'movement_in_period', 'days_cover', 'supplier']
        csv_rows = [
            [
                r['item'].name, r['unit'], r['stock'], r['cost'],
                r['stock_value'], r['movement'],
                f"{r['days_cover']:.1f}" if r['days_cover'] is not None else 'no movement',
                r['supplier'],
            ]
            for r in rows
        ]
        return csv_response(
            f'slow_movers_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, csv_rows,
        )

    return render(request, 'reports/slow_movers.html', {
        'start': start, 'end': end, 'preset': preset,
        'rows': rows,
        'flagged_value': flagged_value,
        'flagged_count': len(rows),
        'days_in_period': days_in_period,
        'threshold_days': threshold_days,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Waste Analysis ─────────────────────────────────────────────────────

@manager_required
def waste_analysis(request):
    """
    Waste in the period broken down three ways: by inventory item, by
    reason, and by staff member who logged it. Uses WasteItem.unit_cost
    (snapshotted at the time of waste) so values reflect the cost on
    the day of the loss, not today's buying price.
    """
    start, end, preset = parse_date_range(request)
    reason_filter = request.GET.get('reason', '')

    waste_items = (
        WasteItem.objects
        .filter(
            waste_log__date__gte=start,
            waste_log__date__lte=end,
        )
        .select_related('inventory_item', 'waste_log', 'waste_log__logged_by')
    )
    if reason_filter:
        waste_items = waste_items.filter(waste_log__reason=reason_filter)

    by_item = {}
    by_reason = {}
    by_staff = {}
    total_cost = Decimal('0')
    total_qty = Decimal('0')
    event_ids = set()

    for wi in waste_items:
        line_cost = wi.cost
        total_cost += line_cost
        total_qty += wi.quantity
        event_ids.add(wi.waste_log_id)

        # By item
        b = by_item.setdefault(wi.inventory_item_id, {
            'name': wi.inventory_item.name,
            'unit': wi.inventory_item.get_unit_display(),
            'qty': Decimal('0'),
            'cost': Decimal('0'),
            'events': 0,
        })
        b['qty'] += wi.quantity
        b['cost'] += line_cost
        b['events'] += 1

        # By reason
        reason = wi.waste_log.get_reason_display()
        r = by_reason.setdefault(reason, {
            'reason': reason,
            'count': 0,
            'cost': Decimal('0'),
        })
        r['count'] += 1
        r['cost'] += line_cost

        # By staff
        logged_by = wi.waste_log.logged_by
        key = logged_by.id if logged_by else None
        username = logged_by.username if logged_by else '(unknown)'
        s = by_staff.setdefault(key, {
            'username': username,
            'count': 0,
            'cost': Decimal('0'),
        })
        s['count'] += 1
        s['cost'] += line_cost

    item_rows = sorted(by_item.values(), key=lambda x: x['cost'], reverse=True)
    reason_rows = sorted(by_reason.values(), key=lambda x: x['cost'], reverse=True)
    staff_rows = sorted(by_staff.values(), key=lambda x: x['cost'], reverse=True)

    # Mix % across each breakdown — sums to 100% within each group.
    for r in item_rows:
        r['pct'] = (r['cost'] / total_cost * 100) if total_cost else Decimal('0')
    for r in reason_rows:
        r['pct'] = (r['cost'] / total_cost * 100) if total_cost else Decimal('0')
    for r in staff_rows:
        r['pct'] = (r['cost'] / total_cost * 100) if total_cost else Decimal('0')

    if request.GET.get('format') == 'csv':
        header = ['breakdown', 'label', 'qty_or_count', 'cost', 'pct']
        rows = []
        for r in item_rows:
            rows.append(['Item', r['name'], r['qty'], r['cost'], f"{r['pct']:.1f}"])
        for r in reason_rows:
            rows.append(['Reason', r['reason'], r['count'], r['cost'], f"{r['pct']:.1f}"])
        for r in staff_rows:
            rows.append(['Staff', r['username'], r['count'], r['cost'], f"{r['pct']:.1f}"])
        return csv_response(
            f'waste_analysis_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, rows,
        )

    return render(request, 'reports/waste_analysis.html', {
        'start': start, 'end': end, 'preset': preset,
        'item_rows': item_rows,
        'reason_rows': reason_rows,
        'staff_rows': staff_rows,
        'total_cost': total_cost,
        'total_qty': total_qty,
        'event_count': len(event_ids),
        'reason_choices': WasteLog.REASON_CHOICES,
        'reason_filter': reason_filter,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Category Performance ───────────────────────────────────────────────

@manager_required
def category_performance(request):
    """
    Revenue, cost, margin, margin % and % of revenue mix per menu
    category over the period. Uses frozen OrderItem.unit_cost.
    """
    start, end, preset = parse_date_range(request)

    qs = OrderItem.objects.filter(
        order__status='paid',
        order__is_comp=False,
        order__created_at__date__gte=start,
        order__created_at__date__lte=end,
    )

    aggregates = (
        qs.values('menu_item__category_id', 'menu_item__category__name')
        .annotate(
            qty_sold=Sum('quantity'),
            revenue=Coalesce(
                Sum(F('unit_price') * F('quantity'), output_field=DecimalField()),
                Decimal('0'), output_field=DecimalField(),
            ),
            cost=Coalesce(
                Sum(F('unit_cost') * F('quantity'), output_field=DecimalField()),
                Decimal('0'), output_field=DecimalField(),
            ),
        )
    )

    rows = []
    total_qty = 0
    total_revenue = Decimal('0')
    total_cost = Decimal('0')
    for a in aggregates:
        revenue = a['revenue'] or Decimal('0')
        cost = a['cost'] or Decimal('0')
        margin = revenue - cost
        margin_pct = (margin / revenue * 100) if revenue else Decimal('0')
        rows.append({
            'category': a['menu_item__category__name'] or 'Uncategorised',
            'qty_sold': a['qty_sold'],
            'revenue': revenue,
            'cost': cost,
            'margin': margin,
            'margin_pct': margin_pct,
        })
        total_qty += a['qty_sold']
        total_revenue += revenue
        total_cost += cost

    # % of revenue mix — based on the period total, computed after sums.
    for r in rows:
        r['mix_pct'] = (r['revenue'] / total_revenue * 100) if total_revenue else Decimal('0')

    total_margin = total_revenue - total_cost
    avg_margin_pct = (total_margin / total_revenue * 100) if total_revenue else Decimal('0')

    sort_key = request.GET.get('sort', 'revenue')
    sort_dir = request.GET.get('dir', 'desc')
    reverse = sort_dir != 'asc'
    sort_field = {
        'category': 'category',
        'qty': 'qty_sold',
        'revenue': 'revenue',
        'cost': 'cost',
        'margin': 'margin',
        'margin_pct': 'margin_pct',
        'mix': 'mix_pct',
    }.get(sort_key, 'revenue')
    rows.sort(key=lambda r: r[sort_field], reverse=reverse)

    if request.GET.get('format') == 'csv':
        header = ['category', 'qty_sold', 'revenue', 'cost', 'margin', 'margin_pct', 'mix_pct']
        csv_rows = [
            [r['category'], r['qty_sold'], r['revenue'], r['cost'],
             r['margin'], f"{r['margin_pct']:.1f}", f"{r['mix_pct']:.1f}"]
            for r in rows
        ]
        return csv_response(
            f'category_performance_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, csv_rows,
        )

    return render(request, 'reports/category_performance.html', {
        'start': start, 'end': end, 'preset': preset,
        'rows': rows,
        'total_qty': total_qty,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'avg_margin_pct': avg_margin_pct,
        'sort_key': sort_key,
        'sort_dir': sort_dir,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Best Selling Items ─────────────────────────────────────────────────

@manager_required
def best_selling(request):
    """
    Top menu items in the period, ranked by quantity sold by default.
    Includes revenue, cost (from frozen OrderItem.unit_cost), margin
    amount and margin %.

    Cost is the snapshotted COGS at the time each unit was sold — not
    the current inventory buying_price — so this report reflects the
    actual profit realised in the period, even if ingredient prices
    have since changed.
    """
    start, end, preset = parse_date_range(request)
    category_filter = request.GET.get('category', '')

    qs = OrderItem.objects.filter(
        order__status='paid',
        order__is_comp=False,
        order__created_at__date__gte=start,
        order__created_at__date__lte=end,
    )
    if category_filter:
        qs = qs.filter(menu_item__category_id=category_filter)

    aggregates = (
        qs.values(
            'menu_item_id',
            'menu_item__title',
            'menu_item__category__name',
        )
        .annotate(
            qty_sold=Sum('quantity'),
            revenue=Coalesce(
                Sum(F('unit_price') * F('quantity'), output_field=DecimalField()),
                Decimal('0'), output_field=DecimalField(),
            ),
            cost=Coalesce(
                Sum(F('unit_cost') * F('quantity'), output_field=DecimalField()),
                Decimal('0'), output_field=DecimalField(),
            ),
        )
    )

    rows = []
    total_qty = 0
    total_revenue = Decimal('0')
    total_cost = Decimal('0')
    for a in aggregates:
        revenue = a['revenue'] or Decimal('0')
        cost = a['cost'] or Decimal('0')
        margin = revenue - cost
        margin_pct = (margin / revenue * 100) if revenue else Decimal('0')
        rows.append({
            'menu_item_id': a['menu_item_id'],
            'title': a['menu_item__title'],
            'category': a['menu_item__category__name'] or 'Uncategorised',
            'qty_sold': a['qty_sold'],
            'revenue': revenue,
            'cost': cost,
            'margin': margin,
            'margin_pct': margin_pct,
        })
        total_qty += a['qty_sold']
        total_revenue += revenue
        total_cost += cost

    total_margin = total_revenue - total_cost
    avg_margin_pct = (total_margin / total_revenue * 100) if total_revenue else Decimal('0')

    # Sortable: qty (default), revenue, cost, margin, margin_pct.
    sort_key = request.GET.get('sort', 'qty')
    sort_dir = request.GET.get('dir', 'desc')
    reverse = sort_dir != 'asc'
    sort_field = {
        'qty': 'qty_sold',
        'revenue': 'revenue',
        'cost': 'cost',
        'margin': 'margin',
        'margin_pct': 'margin_pct',
    }.get(sort_key, 'qty_sold')
    rows.sort(key=lambda r: r[sort_field], reverse=reverse)

    if request.GET.get('format') == 'csv':
        header = ['category', 'item', 'qty_sold', 'revenue', 'cost', 'margin', 'margin_pct']
        csv_rows = [
            [
                r['category'], r['title'], r['qty_sold'],
                r['revenue'], r['cost'], r['margin'], f"{r['margin_pct']:.1f}",
            ]
            for r in rows
        ]
        return csv_response(
            f'best_selling_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, csv_rows,
        )

    from menu.models import Category
    categories = Category.objects.order_by('name')

    return render(request, 'reports/best_selling.html', {
        'start': start, 'end': end, 'preset': preset,
        'rows': rows,
        'total_qty': total_qty,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_margin': total_margin,
        'avg_margin_pct': avg_margin_pct,
        'categories': categories,
        'category_filter': category_filter,
        'item_count': len(rows),
        'sort_key': sort_key,
        'sort_dir': sort_dir,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Menu Margin ────────────────────────────────────────────────────────

@manager_required
def menu_margin(request):
    """
    Per-menu-item cost (from linked inventory item or recipe), selling
    price, margin amount and margin %.

    Cost source = MenuItem.current_unit_cost(): direct-sale items use the
    linked inventory item's buying_price; prepared items roll up the
    recipe ingredients' buying_price × quantity_required. Items with no
    inventory link and no recipe show cost = 0 and are flagged as
    untracked (their margin % is not meaningful).
    """
    show_untracked = request.GET.get('show_untracked') == '1'
    category_filter = request.GET.get('category', '')

    items = (
        MenuItem.objects
        .select_related('category', 'inventory_item')
        .prefetch_related('recipe_items__inventory_item')
        .order_by('category__name', 'title')
    )
    if category_filter:
        items = items.filter(category_id=category_filter)

    rows = []
    total_cost = Decimal('0')
    total_price = Decimal('0')
    for mi in items:
        cost = mi.current_unit_cost()
        price = mi.price or Decimal('0')
        margin = price - cost
        if price > 0:
            margin_pct = (margin / price * 100)
        else:
            margin_pct = None
        untracked = not mi.tracks_stock
        if untracked and not show_untracked:
            continue
        rows.append({
            'menu_item': mi,
            'category': mi.category.name if mi.category else 'Uncategorised',
            'cost': cost,
            'price': price,
            'margin': margin,
            'margin_pct': margin_pct,
            'untracked': untracked,
            'is_direct_sale': mi.is_direct_sale,
        })
        total_cost += cost
        total_price += price

    total_margin = total_price - total_cost
    avg_margin_pct = (total_margin / total_price * 100) if total_price else Decimal('0')

    # Sort: default is category then title (already applied by the queryset).
    # Sortable columns: margin, margin_pct. None values sort to the end
    # regardless of direction so untracked rows don't pollute the top.
    sort_key = request.GET.get('sort', '')
    sort_dir = request.GET.get('dir', 'desc')
    reverse = sort_dir != 'asc'
    if sort_key in ('margin', 'margin_pct'):
        def _key(r):
            v = r[sort_key]
            # None → push to end in both directions
            none_rank = 1 if v is None else 0
            return (none_rank, v if v is not None else Decimal('0'))
        rows.sort(key=_key, reverse=reverse)
        # If we reversed, None items got pushed to the top because their
        # none_rank flipped — fix by partitioning.
        if reverse:
            tracked = [r for r in rows if r[sort_key] is not None]
            untracked_rows = [r for r in rows if r[sort_key] is None]
            rows = tracked + untracked_rows

    if request.GET.get('format') == 'csv':
        header = ['category', 'item', 'source', 'cost', 'price', 'margin', 'margin_pct']
        csv_rows = [
            [
                r['category'],
                r['menu_item'].title,
                'Direct' if r['is_direct_sale'] else ('Recipe' if not r['untracked'] else 'Untracked'),
                r['cost'],
                r['price'],
                r['margin'],
                f"{r['margin_pct']:.1f}" if r['margin_pct'] is not None else '',
            ]
            for r in rows
        ]
        return csv_response('menu_margin.csv', header, csv_rows)

    from menu.models import Category
    categories = Category.objects.order_by('name')

    return render(request, 'reports/menu_margin.html', {
        'rows': rows,
        'total_cost': total_cost,
        'total_price': total_price,
        'total_margin': total_margin,
        'avg_margin_pct': avg_margin_pct,
        'show_untracked': show_untracked,
        'categories': categories,
        'category_filter': category_filter,
        'item_count': len(rows),
        'sort_key': sort_key,
        'sort_dir': sort_dir,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })


# ── Online Sales ───────────────────────────────────────────────────────

# Sources considered "online marketplace" orders. Excludes phone (manual
# call-ins) and other (catch-all) so this report stays focused on the
# delivery-platform pipeline that has its own receivable accounts.
ONLINE_SOURCES = ['ubereats', 'glovo', 'bolt', 'jumia']
ONLINE_AR_ACCOUNTS = ['ubereats_ar', 'glovo_ar', 'bolt_ar', 'jumia_ar']
SOURCE_TO_AR = dict(zip(ONLINE_SOURCES, ONLINE_AR_ACCOUNTS))


@manager_required
def online_sales(request):
    """
    Online sales: orders sourced from Uber Eats / Glovo / Bolt / Jumia.

    Pairs the period's order activity with the live receivable balance
    per platform and the settlement transactions in the period so an
    owner can see at a glance how much each platform owes them, what
    settled, and what's still outstanding.
    """
    start, end, preset = parse_date_range(request)

    paid_orders = (
        Order.objects
        .filter(
            status='paid',
            source__in=ONLINE_SOURCES,
            created_at__date__gte=start,
            created_at__date__lte=end,
        )
        .prefetch_related('items')
    )

    source_labels = dict(Order.SOURCE_CHOICES)

    # Per-platform aggregation across the period.
    per_platform = {
        s: {'count': 0, 'revenue': Decimal('0'), 'avg_ticket': Decimal('0')}
        for s in ONLINE_SOURCES
    }
    total_revenue = Decimal('0')
    total_count = 0
    for order in paid_orders:
        revenue = order.get_total()
        per_platform[order.source]['count'] += 1
        per_platform[order.source]['revenue'] += revenue
        total_revenue += revenue
        total_count += 1
    for s, agg in per_platform.items():
        if agg['count']:
            agg['avg_ticket'] = agg['revenue'] / agg['count']

    # Live outstanding receivable per platform (queried fresh — the AR
    # balance is independent of the period filter; it always reflects
    # the current unsettled total).
    ar_balances = {}
    total_outstanding = Decimal('0')
    for ar_type in ONLINE_AR_ACCOUNTS:
        acct = Account.get_by_type(ar_type)
        bal = acct.balance
        ar_balances[ar_type] = bal
        total_outstanding += bal

    # Settlement transactions in the period — debits on AR accounts
    # represent the platform paying out (via Transfer Funds).
    settlements_qs = (
        Transaction.objects
        .filter(
            account__account_type__in=ONLINE_AR_ACCOUNTS,
            transaction_type='debit',
            created_at__date__gte=start,
            created_at__date__lte=end,
        )
        .select_related('account', 'created_by')
        .order_by('-created_at')
    )

    settlement_rows = []
    settlement_total = Decimal('0')
    settlement_by_platform = {s: Decimal('0') for s in ONLINE_SOURCES}
    for txn in settlements_qs:
        platform_code = txn.account.account_type.replace('_ar', '')
        settlement_rows.append({
            'date': txn.created_at,
            'platform': source_labels.get(platform_code, platform_code),
            'platform_code': platform_code,
            'amount': txn.amount,
            'description': txn.description,
            'by': txn.created_by.username if txn.created_by else 'System',
        })
        settlement_total += txn.amount
        if platform_code in settlement_by_platform:
            settlement_by_platform[platform_code] += txn.amount

    # Combine everything into one row per platform for the summary table.
    platform_rows = []
    for s in ONLINE_SOURCES:
        agg = per_platform[s]
        if not agg['count'] and not ar_balances[SOURCE_TO_AR[s]] and not settlement_by_platform[s]:
            continue
        platform_rows.append({
            'key': s,
            'label': source_labels[s],
            'count': agg['count'],
            'revenue': agg['revenue'],
            'avg_ticket': agg['avg_ticket'],
            'outstanding': ar_balances[SOURCE_TO_AR[s]],
            'settled_in_period': settlement_by_platform[s],
        })
    platform_rows.sort(key=lambda r: r['revenue'], reverse=True)

    if request.GET.get('format') == 'csv':
        header = ['Platform', 'Orders', 'Revenue', 'Avg ticket',
                  'Settled in period', 'Outstanding now']
        rows = [
            [r['label'], r['count'], r['revenue'], r['avg_ticket'],
             r['settled_in_period'], r['outstanding']]
            for r in platform_rows
        ]
        return csv_response(
            f'online_sales_{start.isoformat()}_to_{end.isoformat()}.csv',
            header, rows,
        )

    return render(request, 'reports/online_sales.html', {
        'start': start, 'end': end, 'preset': preset,
        'platform_rows': platform_rows,
        'settlement_rows': settlement_rows,
        'total_revenue': total_revenue,
        'total_count': total_count,
        'total_outstanding': total_outstanding,
        'settlement_total': settlement_total,
        'currency_symbol': RestaurantSettings.load().currency_symbol,
    })
