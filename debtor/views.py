from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction

from core.permissions import (
    manager_required,
    full_access_required as superuser_only,
)
from .models import Debtor, DebtorTransaction, DebtorPaymentAllocation
from .forms import DebtorForm, DebtorTransactionForm


# ── Debtor list ──────────────────────────────────────────────────────

@manager_required
def debtor_list(request):
    show = request.GET.get('show', 'active')
    qs = Debtor.objects.filter(branch=request.branch).select_related('branch')

    if show != 'all':
        qs = qs.filter(is_active=True)

    return render(request, 'debtor/debtor_list.html', {
        'debtors': qs,
        'show': show,
        'is_overall': False,
        'branches': [],
        'branch_filter': '',
    })


# ── Debtor create / edit ────────────────────────────────────────────

@superuser_only
def debtor_create(request):
    from branches.utils import resolve_branch
    if request.method == 'POST':
        form = DebtorForm(request.POST)
        if form.is_valid():
            debtor = form.save(commit=False)
            debtor.branch = resolve_branch(request)
            debtor.save()
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

@manager_required
def debtor_detail(request, pk):
    is_overall = request.user.is_superuser or request.user.groups.filter(name='Overall Manager').exists()
    if is_overall:
        debtor = get_object_or_404(Debtor, pk=pk)
    else:
        debtor = get_object_or_404(Debtor, pk=pk, branch=request.branch)
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

            from menu.models import RestaurantSettings
            symbol = RestaurantSettings.load().currency_symbol
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
        try:
            payment_amount = Decimal(payment_amount).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid payment amount.')
            return redirect('debtor-receive-payment', pk=debtor.pk)

        if payment_amount <= 0:
            messages.error(request, 'Payment amount must be greater than zero.')
            return redirect('debtor-receive-payment', pk=debtor.pk)

        from branches.utils import resolve_branch
        target_branch = resolve_branch(request)

        with transaction.atomic():
            # Create the payment (credit) transaction
            payment_txn = DebtorTransaction.objects.create(
                debtor=debtor,
                transaction_type='credit',
                amount=payment_amount,
                description=payment_note or f'Payment received from {debtor.name}',
                created_by=request.user,
            )

            # Allocate payment against invoices (oldest first)
            remaining_payment = payment_amount
            for inv in sorted(unpaid_invoices, key=lambda x: x.date):
                if remaining_payment <= 0:
                    break
                inv_remaining = inv.remaining
                allocated = min(remaining_payment, inv_remaining)
                DebtorPaymentAllocation.objects.create(
                    payment=payment_txn,
                    invoice=inv,
                    amount=allocated,
                )
                inv.amount_paid += allocated
                inv.save()
                remaining_payment -= allocated

            # Credit the cash account
            from administration.models import Account, Transaction as AcctTransaction
            cash_account = Account.get_by_type('cash', branch=target_branch)
            AcctTransaction.objects.create(
                account=cash_account,
                transaction_type='credit',
                amount=payment_amount,
                description=f'Debtor payment — {debtor.name}',
                reference_type='debtor_payment',
                reference_id=payment_txn.id,
                created_by=request.user,
                branch=target_branch,
            )

        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        messages.success(request, f'Payment of {symbol} {payment_amount:,.2f} received from {debtor.name}.')
        return redirect('debtor-detail', pk=debtor.pk)

    from menu.models import RestaurantSettings
    symbol = RestaurantSettings.load().currency_symbol

    context = {
        'debtor': debtor,
        'unpaid_invoices': unpaid_invoices,
        'total_outstanding': sum(inv.remaining for inv in unpaid_invoices),
        'currency_symbol': symbol,
    }
    return render(request, 'debtor/receive_payment.html', context)
