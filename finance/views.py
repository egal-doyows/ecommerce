from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.shortcuts import redirect, render
from django.utils import timezone as tz

from administration.models import Account, Transaction
from branches.models import Branch
from debtor.models import Debtor, DebtorTransaction
from expenses.models import Expense, ExpenseCategory
from menu.models import Order, OrderItem, RestaurantSettings
from purchasing.models import PurchaseOrder
from staff_compensation.models import PaymentRecord
from supplier.models import Supplier, SupplierTransaction
from tax.models import TaxConfiguration
from wastage.models import WasteLog, WasteItem


from core.permissions import (
    is_overall_manager,
    overall_manager_required as manager_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from core.utils import parse_date_range as _parse_date_range, parse_date


def _resolve_branches(request):
    is_overall = is_overall_manager(request.user)
    branches = Branch.objects.filter(is_active=True) if is_overall else []
    branch_filter = request.GET.get('branch', '')
    selected_branch = None
    if is_overall and branch_filter:
        try:
            selected_branch = Branch.objects.get(pk=branch_filter)
        except Branch.DoesNotExist:
            pass
    return is_overall, branches, selected_branch, branch_filter


def _branch_qs(qs, request, selected_branch, is_overall):
    if selected_branch:
        return qs.filter(branch=selected_branch)
    if not is_overall:
        return qs.filter(branch=request.branch)
    return qs


def _common_ctx(request):
    restaurant = RestaurantSettings.load()
    d_from, d_to, date_from, date_to = _parse_date_range(request)
    is_overall, branches, selected_branch, branch_filter = _resolve_branches(request)
    return {
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
        'd_from': d_from,
        'd_to': d_to,
        'date_from': date_from,
        'date_to': date_to,
        'is_overall': is_overall,
        'branches': branches,
        'selected_branch': selected_branch,
        'branch_filter': branch_filter,
    }


# ---------------------------------------------------------------------------
# Dashboard — Financial Overview
# ---------------------------------------------------------------------------

@manager_required
def finance_dashboard(request):
    ctx = _common_ctx(request)
    d_from, d_to = ctx['d_from'], ctx['d_to']
    cs = ctx['currency_symbol']
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    # Revenue & Tax
    orders = _branch_qs(
        Order.objects.filter(status='paid', created_at__date__gte=d_from, created_at__date__lte=d_to),
        request, selected_branch, is_overall,
    ).prefetch_related('items')
    total_revenue = sum(o.get_total() for o in orders)
    total_tax_collected = orders.aggregate(t=models.Sum('tax_amount'))['t'] or Decimal('0')
    total_revenue_excl_tax = total_revenue - total_tax_collected
    order_count = orders.count()

    # Expenses
    expenses = _branch_qs(
        Expense.objects.filter(status='approved', date__gte=d_from, date__lte=d_to),
        request, selected_branch, is_overall,
    )
    total_expenses = expenses.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    # Wastage cost
    waste_items = _branch_qs(
        WasteItem.objects.filter(waste_log__date__gte=d_from, waste_log__date__lte=d_to),
        request, selected_branch, is_overall,
    ).select_related('waste_log')
    # WasteItem doesn't have branch directly — filter via waste_log
    if selected_branch:
        waste_items = WasteItem.objects.filter(
            waste_log__date__gte=d_from, waste_log__date__lte=d_to,
            waste_log__branch=selected_branch,
        )
    elif not is_overall:
        waste_items = WasteItem.objects.filter(
            waste_log__date__gte=d_from, waste_log__date__lte=d_to,
            waste_log__branch=request.branch,
        )
    else:
        waste_items = WasteItem.objects.filter(
            waste_log__date__gte=d_from, waste_log__date__lte=d_to,
        )
    total_wastage = sum(wi.cost for wi in waste_items)

    # Staff compensation
    payroll_qs = PaymentRecord.objects.filter(
        status='paid', period_start__lte=d_to, period_end__gte=d_from,
    )
    if selected_branch:
        payroll_qs = payroll_qs.filter(branch=selected_branch)
    elif not is_overall:
        payroll_qs = payroll_qs.filter(branch=request.branch)
    total_payroll = payroll_qs.aggregate(t=models.Sum('amount_paid'))['t'] or Decimal('0')

    # Supplier payments (money out)
    supplier_payments = SupplierTransaction.objects.filter(
        transaction_type='credit', date__gte=d_from, date__lte=d_to,
    )
    if selected_branch:
        supplier_payments = supplier_payments.filter(branch=selected_branch)
    elif not is_overall:
        supplier_payments = supplier_payments.filter(branch=request.branch)
    total_supplier_payments = supplier_payments.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    # Debtor collections (money in)
    debtor_collections = DebtorTransaction.objects.filter(
        transaction_type='credit', date__gte=d_from, date__lte=d_to,
    )
    if selected_branch:
        debtor_collections = debtor_collections.filter(debtor__branch=selected_branch)
    elif not is_overall:
        debtor_collections = debtor_collections.filter(debtor__branch=request.branch)
    total_debtor_collections = debtor_collections.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    # Account balances
    accounts_qs = Account.objects.filter(is_active=True)
    if selected_branch:
        accounts_qs = accounts_qs.filter(branch=selected_branch)
    elif not is_overall:
        accounts_qs = accounts_qs.filter(branch=request.branch)
    account_balances = []
    total_balance = Decimal('0')
    for acc in accounts_qs:
        bal = acc.balance
        total_balance += bal
        account_balances.append({'name': acc.name, 'type': acc.get_account_type_display(), 'balance': bal})

    # Net profit = revenue (excl tax) - expenses - wastage - payroll
    total_costs = total_expenses + total_wastage + total_payroll
    net_profit = total_revenue_excl_tax - total_costs

    # Revenue by payment method
    payment_breakdown = []
    for code, label in Order.PAYMENT_CHOICES:
        method_orders = orders.filter(payment_method=code)
        method_total = sum(o.get_total() for o in method_orders)
        if method_total > 0:
            pct = round(float(method_total) / float(total_revenue) * 100, 1) if total_revenue else 0
            payment_breakdown.append({'method': label, 'total': method_total, 'pct': pct})

    # Branch comparison
    branch_comparison = []
    if is_overall and not selected_branch:
        for branch in Branch.objects.filter(is_active=True):
            b_orders = Order.objects.filter(
                status='paid', created_at__date__gte=d_from, created_at__date__lte=d_to, branch=branch,
            ).prefetch_related('items')
            b_revenue = sum(o.get_total() for o in b_orders)
            b_expenses = Expense.objects.filter(
                status='approved', date__gte=d_from, date__lte=d_to, branch=branch,
            ).aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
            b_waste = sum(
                wi.cost for wi in WasteItem.objects.filter(
                    waste_log__date__gte=d_from, waste_log__date__lte=d_to, waste_log__branch=branch,
                )
            )
            b_payroll = PaymentRecord.objects.filter(
                status='paid', period_start__lte=d_to, period_end__gte=d_from, branch=branch,
            ).aggregate(t=models.Sum('amount_paid'))['t'] or Decimal('0')
            b_tax = b_orders.aggregate(t=models.Sum('tax_amount'))['t'] or Decimal('0')
            b_revenue_excl = b_revenue - b_tax
            b_profit = b_revenue_excl - b_expenses - b_waste - b_payroll
            branch_comparison.append({
                'name': branch.name,
                'revenue': b_revenue,
                'tax': b_tax,
                'net_revenue': b_revenue_excl,
                'expenses': b_expenses,
                'wastage': b_waste,
                'payroll': b_payroll,
                'profit': b_profit,
                'orders': b_orders.count(),
            })
        branch_comparison.sort(key=lambda x: x['revenue'], reverse=True)

    tax_cfg = TaxConfiguration.load()
    ctx.update({
        'total_revenue': total_revenue,
        'total_revenue_excl_tax': total_revenue_excl_tax,
        'total_tax_collected': total_tax_collected,
        'tax_config': tax_cfg,
        'order_count': order_count,
        'total_expenses': total_expenses,
        'total_wastage': total_wastage,
        'total_payroll': total_payroll,
        'total_supplier_payments': total_supplier_payments,
        'total_debtor_collections': total_debtor_collections,
        'net_profit': net_profit,
        'total_costs': total_costs,
        'account_balances': account_balances,
        'total_balance': total_balance,
        'payment_breakdown': payment_breakdown,
        'branch_comparison': branch_comparison,
    })
    return render(request, 'finance/dashboard.html', ctx)


# ---------------------------------------------------------------------------
# Profit & Loss
# ---------------------------------------------------------------------------

@manager_required
def profit_loss(request):
    ctx = _common_ctx(request)
    d_from, d_to = ctx['d_from'], ctx['d_to']
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    # REVENUE
    orders = _branch_qs(
        Order.objects.filter(status='paid', created_at__date__gte=d_from, created_at__date__lte=d_to),
        request, selected_branch, is_overall,
    ).prefetch_related('items', 'items__menu_item__category')

    total_revenue_gross = sum(o.get_total() for o in orders)
    total_tax_collected = orders.aggregate(t=models.Sum('tax_amount'))['t'] or Decimal('0')
    total_revenue = total_revenue_gross - total_tax_collected  # Revenue excluding tax

    # Revenue by category
    category_revenue = defaultdict(Decimal)
    for order in orders:
        for item in order.items.all():
            cat_name = item.menu_item.category.name if item.menu_item.category else 'Uncategorized'
            category_revenue[cat_name] += item.get_subtotal()
    revenue_by_category = sorted(category_revenue.items(), key=lambda x: x[1], reverse=True)

    # COST OF GOODS (wastage)
    if selected_branch:
        waste_items = WasteItem.objects.filter(
            waste_log__date__gte=d_from, waste_log__date__lte=d_to, waste_log__branch=selected_branch)
    elif not is_overall:
        waste_items = WasteItem.objects.filter(
            waste_log__date__gte=d_from, waste_log__date__lte=d_to, waste_log__branch=request.branch)
    else:
        waste_items = WasteItem.objects.filter(
            waste_log__date__gte=d_from, waste_log__date__lte=d_to)
    total_cogs = sum(wi.cost for wi in waste_items)

    gross_profit = total_revenue - total_cogs

    # OPERATING EXPENSES
    expenses_qs = _branch_qs(
        Expense.objects.filter(status='approved', date__gte=d_from, date__lte=d_to),
        request, selected_branch, is_overall,
    ).select_related('category')

    expense_by_category = defaultdict(Decimal)
    for exp in expenses_qs:
        cat_name = exp.category.name if exp.category else 'Uncategorized'
        expense_by_category[cat_name] += exp.amount
    expense_categories = sorted(expense_by_category.items(), key=lambda x: x[1], reverse=True)
    total_expenses = sum(v for _, v in expense_categories)

    # PAYROLL
    payroll_qs = PaymentRecord.objects.filter(
        status='paid', period_start__lte=d_to, period_end__gte=d_from)
    if selected_branch:
        payroll_qs = payroll_qs.filter(branch=selected_branch)
    elif not is_overall:
        payroll_qs = payroll_qs.filter(branch=request.branch)
    total_payroll = payroll_qs.aggregate(t=models.Sum('amount_paid'))['t'] or Decimal('0')

    total_operating = total_expenses + total_payroll
    operating_profit = gross_profit - total_operating
    net_profit = operating_profit

    # Margin
    gross_margin = round(float(gross_profit) / float(total_revenue) * 100, 1) if total_revenue else Decimal('0')
    net_margin = round(float(net_profit) / float(total_revenue) * 100, 1) if total_revenue else Decimal('0')

    tax_cfg = TaxConfiguration.load()
    ctx.update({
        'total_revenue_gross': total_revenue_gross,
        'total_revenue': total_revenue,
        'total_tax_collected': total_tax_collected,
        'tax_config': tax_cfg,
        'revenue_by_category': revenue_by_category,
        'total_cogs': total_cogs,
        'gross_profit': gross_profit,
        'gross_margin': gross_margin,
        'expense_categories': expense_categories,
        'total_expenses': total_expenses,
        'total_payroll': total_payroll,
        'total_operating': total_operating,
        'operating_profit': operating_profit,
        'net_profit': net_profit,
        'net_margin': net_margin,
    })
    return render(request, 'finance/profit_loss.html', ctx)


# ---------------------------------------------------------------------------
# Cash Flow
# ---------------------------------------------------------------------------

@manager_required
def cash_flow(request):
    ctx = _common_ctx(request)
    d_from, d_to = ctx['d_from'], ctx['d_to']
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    txns = Transaction.objects.filter(created_at__date__gte=d_from, created_at__date__lte=d_to)
    if selected_branch:
        txns = txns.filter(branch=selected_branch)
    elif not is_overall:
        txns = txns.filter(branch=request.branch)

    # Inflows vs outflows by account
    accounts_qs = Account.objects.filter(is_active=True)
    if selected_branch:
        accounts_qs = accounts_qs.filter(branch=selected_branch)
    elif not is_overall:
        accounts_qs = accounts_qs.filter(branch=request.branch)

    account_flows = []
    total_inflow = Decimal('0')
    total_outflow = Decimal('0')
    for acc in accounts_qs:
        acc_txns = txns.filter(account=acc)
        agg = acc_txns.aggregate(
            inflow=models.Sum('amount', filter=models.Q(transaction_type='credit')),
            outflow=models.Sum('amount', filter=models.Q(transaction_type='debit')),
        )
        inflow = agg['inflow'] or Decimal('0')
        outflow = agg['outflow'] or Decimal('0')
        total_inflow += inflow
        total_outflow += outflow
        account_flows.append({
            'name': acc.name,
            'type': acc.get_account_type_display(),
            'inflow': inflow,
            'outflow': outflow,
            'net': inflow - outflow,
            'balance': acc.balance,
        })

    net_flow = total_inflow - total_outflow

    # Inflows by source
    inflow_by_source = defaultdict(Decimal)
    outflow_by_source = defaultdict(Decimal)
    for txn in txns:
        label = txn.reference_type or 'other'
        source_labels = {
            'order': 'Sales Revenue',
            'staff_payment': 'Staff Payments',
            'expense': 'Operating Expenses',
            'supplier_payment': 'Supplier Payments',
            'manual': 'Manual Adjustments',
            'other': 'Other',
        }
        name = source_labels.get(label, label.replace('_', ' ').title())
        if txn.transaction_type == 'credit':
            inflow_by_source[name] += txn.amount
        else:
            outflow_by_source[name] += txn.amount

    inflow_sources = sorted(inflow_by_source.items(), key=lambda x: x[1], reverse=True)
    outflow_sources = sorted(outflow_by_source.items(), key=lambda x: x[1], reverse=True)

    # Tax collected from orders in this period
    tax_config = TaxConfiguration.load()
    orders = Order.objects.filter(status='paid', created_at__date__gte=d_from, created_at__date__lte=d_to)
    if selected_branch:
        orders = orders.filter(branch=selected_branch)
    elif not is_overall:
        orders = orders.filter(branch=request.branch)
    total_tax_collected = orders.aggregate(t=models.Sum('tax_amount'))['t'] or Decimal('0')

    # Daily flow trend
    daily_flow = []
    current = d_from
    while current <= d_to:
        day_txns = txns.filter(created_at__date=current)
        agg = day_txns.aggregate(
            inflow=models.Sum('amount', filter=models.Q(transaction_type='credit')),
            outflow=models.Sum('amount', filter=models.Q(transaction_type='debit')),
        )
        day_in = agg['inflow'] or Decimal('0')
        day_out = agg['outflow'] or Decimal('0')
        daily_flow.append({
            'date': current,
            'label': current.strftime('%d %b'),
            'inflow': day_in,
            'outflow': day_out,
            'net': day_in - day_out,
        })
        current += timedelta(days=1)

    ctx.update({
        'account_flows': account_flows,
        'total_inflow': total_inflow,
        'total_outflow': total_outflow,
        'net_flow': net_flow,
        'inflow_sources': inflow_sources,
        'outflow_sources': outflow_sources,
        'daily_flow': daily_flow,
        'total_tax_collected': total_tax_collected,
        'tax_config': tax_config,
    })
    return render(request, 'finance/cash_flow.html', ctx)


# ---------------------------------------------------------------------------
# Expense Report
# ---------------------------------------------------------------------------

@manager_required
def expense_report(request):
    ctx = _common_ctx(request)
    d_from, d_to = ctx['d_from'], ctx['d_to']
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    expenses = _branch_qs(
        Expense.objects.filter(date__gte=d_from, date__lte=d_to),
        request, selected_branch, is_overall,
    ).select_related('category', 'recorded_by')

    total_approved = expenses.filter(status='approved').aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    total_pending = expenses.filter(status='pending').aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    total_rejected = expenses.filter(status='rejected').aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    # By category
    by_category = defaultdict(lambda: {'approved': Decimal('0'), 'pending': Decimal('0'), 'count': 0})
    for exp in expenses.filter(status='approved'):
        cat = exp.category.name if exp.category else 'Uncategorized'
        by_category[cat]['approved'] += exp.amount
        by_category[cat]['count'] += 1
    category_breakdown = sorted(by_category.items(), key=lambda x: x[1]['approved'], reverse=True)

    # By payment method
    by_method = defaultdict(Decimal)
    method_labels = dict(Expense.PAYMENT_METHOD_CHOICES)
    for exp in expenses.filter(status='approved'):
        by_method[method_labels.get(exp.payment_method, exp.payment_method)] += exp.amount
    method_breakdown = sorted(by_method.items(), key=lambda x: x[1], reverse=True)

    # Top vendors
    vendor_totals = defaultdict(lambda: {'total': Decimal('0'), 'count': 0})
    for exp in expenses.filter(status='approved'):
        vendor = exp.vendor or 'Unknown'
        vendor_totals[vendor]['total'] += exp.amount
        vendor_totals[vendor]['count'] += 1
    top_vendors = sorted(vendor_totals.items(), key=lambda x: x[1]['total'], reverse=True)[:10]

    # Branch breakdown
    branch_expenses = []
    if is_overall and not selected_branch:
        for branch in Branch.objects.filter(is_active=True):
            b_total = expenses.filter(status='approved', branch=branch).aggregate(
                t=models.Sum('amount'))['t'] or Decimal('0')
            if b_total > 0:
                branch_expenses.append({'name': branch.name, 'total': b_total})
        branch_expenses.sort(key=lambda x: x['total'], reverse=True)

    ctx.update({
        'total_approved': total_approved,
        'total_pending': total_pending,
        'total_rejected': total_rejected,
        'expense_count': expenses.filter(status='approved').count(),
        'category_breakdown': category_breakdown,
        'method_breakdown': method_breakdown,
        'top_vendors': top_vendors,
        'branch_expenses': branch_expenses,
    })
    return render(request, 'finance/expense_report.html', ctx)


# ---------------------------------------------------------------------------
# Wastage Report
# ---------------------------------------------------------------------------

@manager_required
def wastage_report(request):
    ctx = _common_ctx(request)
    d_from, d_to = ctx['d_from'], ctx['d_to']
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    logs = WasteLog.objects.filter(date__gte=d_from, date__lte=d_to)
    if selected_branch:
        logs = logs.filter(branch=selected_branch)
    elif not is_overall:
        logs = logs.filter(branch=request.branch)
    logs = logs.prefetch_related('items', 'items__inventory_item')

    waste_items = WasteItem.objects.filter(waste_log__in=logs).select_related('inventory_item', 'waste_log')

    total_cost = sum(wi.cost for wi in waste_items)
    total_events = logs.count()
    total_items_wasted = waste_items.count()

    # By reason
    by_reason = []
    for code, label in WasteLog.REASON_CHOICES:
        reason_items = waste_items.filter(waste_log__reason=code)
        cost = sum(wi.cost for wi in reason_items)
        count = reason_items.count()
        if count > 0:
            by_reason.append({
                'reason': label, 'code': code, 'count': count, 'cost': cost,
                'pct': round(float(cost) / float(total_cost) * 100, 1) if total_cost else 0,
            })
    by_reason.sort(key=lambda x: x['cost'], reverse=True)

    # Top wasted items
    item_totals = {}
    for wi in waste_items:
        key = wi.inventory_item.pk
        if key not in item_totals:
            item_totals[key] = {
                'name': wi.inventory_item.name,
                'unit': wi.inventory_item.get_unit_display(),
                'total_qty': Decimal('0'), 'total_cost': Decimal('0'), 'count': 0,
            }
        item_totals[key]['total_qty'] += wi.quantity
        item_totals[key]['total_cost'] += wi.cost
        item_totals[key]['count'] += 1
    top_items = sorted(item_totals.values(), key=lambda x: x['total_cost'], reverse=True)[:15]

    # Branch breakdown
    branch_wastage = []
    if is_overall and not selected_branch:
        for branch in Branch.objects.filter(is_active=True):
            b_items = waste_items.filter(waste_log__branch=branch)
            b_cost = sum(wi.cost for wi in b_items)
            if b_cost > 0:
                branch_wastage.append({
                    'name': branch.name, 'cost': b_cost, 'events': logs.filter(branch=branch).count(),
                })
        branch_wastage.sort(key=lambda x: x['cost'], reverse=True)

    # Wastage as % of revenue (excluding tax for accuracy)
    orders = Order.objects.filter(status='paid', created_at__date__gte=d_from, created_at__date__lte=d_to)
    if selected_branch:
        orders = orders.filter(branch=selected_branch)
    elif not is_overall:
        orders = orders.filter(branch=request.branch)
    total_revenue_gross = sum(o.get_total() for o in orders.prefetch_related('items'))
    total_tax_collected = orders.aggregate(t=models.Sum('tax_amount'))['t'] or Decimal('0')
    total_revenue = total_revenue_gross - total_tax_collected
    waste_pct = round(float(total_cost) / float(total_revenue) * 100, 1) if total_revenue else Decimal('0')

    tax_config = TaxConfiguration.load()

    ctx.update({
        'total_cost': total_cost,
        'total_events': total_events,
        'total_items_wasted': total_items_wasted,
        'by_reason': by_reason,
        'top_items': top_items,
        'branch_wastage': branch_wastage,
        'total_revenue': total_revenue,
        'waste_pct': waste_pct,
        'total_tax_collected': total_tax_collected,
        'tax_config': tax_config,
    })
    return render(request, 'finance/wastage_report.html', ctx)


# ---------------------------------------------------------------------------
# Payroll Report
# ---------------------------------------------------------------------------

@manager_required
def payroll_report(request):
    ctx = _common_ctx(request)
    d_from, d_to = ctx['d_from'], ctx['d_to']
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    records = PaymentRecord.objects.filter(
        period_start__lte=d_to, period_end__gte=d_from,
    ).select_related('staff')
    if selected_branch:
        records = records.filter(branch=selected_branch)
    elif not is_overall:
        records = records.filter(branch=request.branch)

    total_owed = records.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    total_paid = records.filter(status='paid').aggregate(t=models.Sum('amount_paid'))['t'] or Decimal('0')
    total_pending = records.filter(status='pending').aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    # By type
    commission_total = records.filter(payment_type='commission').aggregate(
        t=models.Sum('amount'))['t'] or Decimal('0')
    salary_total = records.filter(payment_type='salary').aggregate(
        t=models.Sum('amount'))['t'] or Decimal('0')

    # Staff breakdown
    staff_data = []
    for rec in records:
        staff_data.append({
            'name': rec.staff.get_full_name() or rec.staff.username,
            'type': rec.get_payment_type_display(),
            'period': rec.month_label,
            'amount': rec.amount,
            'paid': rec.amount_paid,
            'remaining': rec.remaining,
            'status': rec.status,
        })

    # By disbursement method
    by_method = defaultdict(Decimal)
    method_labels = dict(PaymentRecord.DISBURSEMENT_METHOD_CHOICES)
    for rec in records.filter(status='paid'):
        method = method_labels.get(rec.disbursement_method, rec.disbursement_method or 'Unknown')
        by_method[method] += rec.amount_paid
    method_breakdown = sorted(by_method.items(), key=lambda x: x[1], reverse=True)

    # Branch breakdown
    branch_payroll = []
    if is_overall and not selected_branch:
        for branch in Branch.objects.filter(is_active=True):
            b_total = records.filter(branch=branch).aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
            b_paid = records.filter(branch=branch, status='paid').aggregate(
                t=models.Sum('amount_paid'))['t'] or Decimal('0')
            if b_total > 0:
                branch_payroll.append({'name': branch.name, 'total': b_total, 'paid': b_paid})
        branch_payroll.sort(key=lambda x: x['total'], reverse=True)

    ctx.update({
        'total_owed': total_owed,
        'total_paid': total_paid,
        'total_pending': total_pending,
        'commission_total': commission_total,
        'salary_total': salary_total,
        'staff_data': staff_data,
        'method_breakdown': method_breakdown,
        'branch_payroll': branch_payroll,
    })
    return render(request, 'finance/payroll_report.html', ctx)


# ---------------------------------------------------------------------------
# Accounts Receivable (Debtors)
# ---------------------------------------------------------------------------

@manager_required
def receivables_report(request):
    ctx = _common_ctx(request)
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    debtors = Debtor.objects.filter(is_active=True)
    if selected_branch:
        debtors = debtors.filter(branch=selected_branch)
    elif not is_overall:
        debtors = debtors.filter(branch=request.branch)

    debtor_data = []
    total_outstanding = Decimal('0')
    total_owed = Decimal('0')
    total_received = Decimal('0')
    for d in debtors:
        bal = d.balance
        if bal != 0 or d.total_owed > 0:
            debtor_data.append({
                'name': d.name,
                'contact': d.contact_person,
                'phone': d.phone,
                'total_owed': d.total_owed,
                'total_received': d.total_received,
                'balance': bal,
            })
            total_outstanding += bal
            total_owed += d.total_owed
            total_received += d.total_received
    debtor_data.sort(key=lambda x: x['balance'], reverse=True)

    # Aging — outstanding invoices
    today = tz.now().date()
    aging = {'current': Decimal('0'), 'days_30': Decimal('0'), 'days_60': Decimal('0'), 'days_90': Decimal('0'), 'over_90': Decimal('0')}
    outstanding_invoices = DebtorTransaction.objects.filter(
        transaction_type='debit',
    ).exclude(amount_paid__gte=models.F('amount'))
    if selected_branch:
        outstanding_invoices = outstanding_invoices.filter(debtor__branch=selected_branch)
    elif not is_overall:
        outstanding_invoices = outstanding_invoices.filter(debtor__branch=request.branch)

    for inv in outstanding_invoices:
        remaining = inv.remaining
        days = (today - inv.date).days
        if days <= 30:
            aging['current'] += remaining
        elif days <= 60:
            aging['days_30'] += remaining
        elif days <= 90:
            aging['days_60'] += remaining
        else:
            aging['over_90'] += remaining

    # Branch breakdown
    branch_receivables = []
    if is_overall and not selected_branch:
        for branch in Branch.objects.filter(is_active=True):
            b_total = sum(d.balance for d in Debtor.objects.filter(is_active=True, branch=branch))
            if b_total > 0:
                branch_receivables.append({'name': branch.name, 'total': b_total})
        branch_receivables.sort(key=lambda x: x['total'], reverse=True)

    ctx.update({
        'debtor_data': debtor_data,
        'total_outstanding': total_outstanding,
        'total_owed': total_owed,
        'total_received': total_received,
        'aging': aging,
        'branch_receivables': branch_receivables,
    })
    return render(request, 'finance/receivables.html', ctx)


# ---------------------------------------------------------------------------
# Accounts Payable (Suppliers)
# ---------------------------------------------------------------------------

@manager_required
def payables_report(request):
    ctx = _common_ctx(request)
    is_overall = ctx['is_overall']
    selected_branch = ctx['selected_branch']

    suppliers = Supplier.objects.filter(is_active=True)
    supplier_data = []
    total_outstanding = Decimal('0')
    total_invoiced = Decimal('0')
    total_paid = Decimal('0')
    for s in suppliers:
        bal = s.balance
        if bal != 0 or s.total_invoiced > 0:
            supplier_data.append({
                'name': s.name,
                'contact': s.contact_person,
                'phone': s.phone,
                'total_invoiced': s.total_invoiced,
                'total_paid': s.total_paid,
                'balance': bal,
            })
            total_outstanding += bal
            total_invoiced += s.total_invoiced
            total_paid += s.total_paid
    supplier_data.sort(key=lambda x: x['balance'], reverse=True)

    # Aging
    today = tz.now().date()
    aging = {'current': Decimal('0'), 'days_30': Decimal('0'), 'days_60': Decimal('0'), 'days_90': Decimal('0'), 'over_90': Decimal('0')}
    outstanding = SupplierTransaction.objects.filter(
        transaction_type='debit',
    ).exclude(amount_paid__gte=models.F('amount'))
    if selected_branch:
        outstanding = outstanding.filter(branch=selected_branch)
    elif not is_overall:
        outstanding = outstanding.filter(branch=request.branch)

    for inv in outstanding:
        remaining = inv.remaining
        days = (today - inv.date).days
        if days <= 30:
            aging['current'] += remaining
        elif days <= 60:
            aging['days_30'] += remaining
        elif days <= 90:
            aging['days_60'] += remaining
        else:
            aging['over_90'] += remaining

    # Purchase orders summary
    po_qs = PurchaseOrder.objects.filter(status__in=['approved', 'received'])
    if selected_branch:
        po_qs = po_qs.filter(branch=selected_branch)
    elif not is_overall:
        po_qs = po_qs.filter(branch=request.branch)
    total_po_value = sum(po.total for po in po_qs.prefetch_related('items'))
    po_count = po_qs.count()

    # Branch breakdown
    branch_payables = []
    if is_overall and not selected_branch:
        for branch in Branch.objects.filter(is_active=True):
            b_invoices = SupplierTransaction.objects.filter(
                transaction_type='debit', branch=branch,
            ).exclude(amount_paid__gte=models.F('amount'))
            b_total = sum(inv.remaining for inv in b_invoices)
            if b_total > 0:
                branch_payables.append({'name': branch.name, 'total': b_total})
        branch_payables.sort(key=lambda x: x['total'], reverse=True)

    ctx.update({
        'supplier_data': supplier_data,
        'total_outstanding': total_outstanding,
        'total_invoiced': total_invoiced,
        'total_paid': total_paid,
        'aging': aging,
        'total_po_value': total_po_value,
        'po_count': po_count,
        'branch_payables': branch_payables,
    })
    return render(request, 'finance/payables.html', ctx)
