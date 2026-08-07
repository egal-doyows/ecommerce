import logging
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.core.exceptions import ValidationError

from .models import Debtor, DebtorTransaction, DebtorPaymentAllocation
from .forms import DebtorForm, DebtorTransactionForm
from .services import record_debtor_payment, reverse_debtor_payment

audit_logger = logging.getLogger('audit')


def _is_manager(user):
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name='Manager').exists())


def _can_manage_debtors(user):
    """Allow superusers, Managers, and Supervisors to view & create debtors."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Manager', 'Supervisor']).exists()


def manager_required(view_func):
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager(request.user):
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def debtor_access_required(view_func):
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _can_manage_debtors(request.user):
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def superuser_only(view_func):
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'Only the administrator can perform this action.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


# ── Debtor list ──────────────────────────────────────────────────────

@debtor_access_required
def debtor_list(request):
    show = request.GET.get('show', 'active')
    if show == 'all':
        debtors = Debtor.objects.all()
    else:
        debtors = Debtor.objects.filter(is_active=True)
    return render(request, 'debtor/debtor_list.html', {
        'debtors': debtors,
        'show': show,
    })


# ── Debtor create / edit ────────────────────────────────────────────

@debtor_access_required
def debtor_create(request):
    if request.method == 'POST':
        form = DebtorForm(request.POST)
        if form.is_valid():
            debtor = form.save(commit=False)
            debtor.created_by = request.user
            debtor.is_active = True
            debtor.save()
            role = (
                'superuser' if request.user.is_superuser
                else ','.join(request.user.groups.values_list('name', flat=True)) or 'no-group'
            )
            audit_logger.info(
                "Debtor created: id=%d name='%s' by=%s (%s)",
                debtor.id, debtor.name, request.user.username, role,
            )
            messages.success(request, 'Debtor created.')
            return redirect('debtor-list')
    else:
        form = DebtorForm()
    return render(request, 'debtor/debtor_form.html', {
        'form': form, 'title': 'Add Debtor',
    })


@superuser_only
def debtor_edit(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)
    if request.method == 'POST':
        form = DebtorForm(request.POST, instance=debtor)
        if form.is_valid():
            form.save()
            messages.success(request, f'{debtor.name} updated.')
            return redirect('debtor-detail', pk=debtor.pk)
    else:
        form = DebtorForm(instance=debtor)
    return render(request, 'debtor/debtor_form.html', {
        'form': form, 'title': f'Edit {debtor.name}',
    })


# ── Debtor detail (account ledger) ──────────────────────────────────

@debtor_access_required
def debtor_detail(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)
    transactions = debtor.transactions.all()

    txn_type = request.GET.get('type')
    if txn_type in ('debit', 'credit'):
        transactions = transactions.filter(transaction_type=txn_type)

    # Unpaid invoices for the summary section
    all_invoices = debtor.transactions.filter(transaction_type='debit')
    unpaid_invoices = [inv for inv in all_invoices if inv.remaining > Decimal('0')]

    return render(request, 'debtor/debtor_detail.html', {
        'debtor': debtor,
        'transactions': transactions,
        'txn_type': txn_type,
        'unpaid_invoices': unpaid_invoices,
    })


# ── Record invoice (debit only — payments go through receive_payment) ─

@superuser_only
def transaction_create(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)
    if request.method == 'POST':
        form = DebtorTransactionForm(request.POST)
        if form.is_valid():
            txn = form.save(commit=False)
            txn.debtor = debtor
            txn.transaction_type = 'debit'
            txn.created_by = request.user
            txn.save()

            from menu.cache import get_restaurant_settings
            symbol = get_restaurant_settings().currency_symbol
            messages.success(request, f'Invoice recorded — {symbol} {txn.amount:,.2f}')
            return redirect('debtor-detail', pk=debtor.pk)
    else:
        form = DebtorTransactionForm()
    return render(request, 'debtor/transaction_form.html', {
        'form': form,
        'debtor': debtor,
    })


# ── Receive payment against invoices ─────────────────────────────────

@superuser_only
def receive_payment(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)

    # Get unpaid invoices (debit transactions with remaining > 0)
    all_invoices = debtor.transactions.filter(transaction_type='debit')
    unpaid_invoices = [inv for inv in all_invoices if inv.remaining > Decimal('0')]

    if request.method == 'POST':
        payment_amount = request.POST.get('payment_amount', '0')
        payment_note = request.POST.get('payment_note', '').strip()
        payment_method = request.POST.get('payment_method', '')
        payment_reference = request.POST.get('payment_reference', '')
        try:
            payment_amount = Decimal(payment_amount).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid payment amount.')
            return redirect('debtor-receive-payment', pk=debtor.pk)

        if payment_amount <= 0:
            messages.error(request, 'Payment amount must be greater than zero.')
            return redirect('debtor-receive-payment', pk=debtor.pk)

        try:
            record_debtor_payment(
                debtor=debtor,
                amount=payment_amount,
                method=payment_method,
                reference=payment_reference,
                note=payment_note,
                created_by=request.user,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect('debtor-receive-payment', pk=debtor.pk)

        from menu.cache import get_restaurant_settings
        symbol = get_restaurant_settings().currency_symbol
        messages.success(request, f'Payment of {symbol} {payment_amount:,.2f} received from {debtor.name}.')
        return redirect('debtor-detail', pk=debtor.pk)

    from menu.cache import get_restaurant_settings
    symbol = get_restaurant_settings().currency_symbol

    context = {
        'debtor': debtor,
        'unpaid_invoices': unpaid_invoices,
        'total_outstanding': sum(inv.remaining for inv in unpaid_invoices),
        'currency_symbol': symbol,
    }
    return render(request, 'debtor/receive_payment.html', context)


@superuser_only
def reverse_payment(request, transaction_id):
    if request.method != 'POST':
        return redirect('debtor-list')
    payment = get_object_or_404(
        DebtorTransaction, pk=transaction_id, transaction_type='credit',
    )
    debtor_id = payment.debtor_id
    amount = payment.amount
    reverse_debtor_payment(payment=payment, created_by=request.user)
    audit_logger.info(
        'Debtor payment reversed: transaction_id=%d amount=%s by=%s',
        transaction_id, amount, request.user.username,
    )
    messages.success(request, f'Payment of {amount:,.2f} reversed.')
    return redirect('debtor-detail', pk=debtor_id)
