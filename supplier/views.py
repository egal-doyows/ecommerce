from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction

from .models import Supplier, SupplierTransaction, SupplierPaymentAllocation
from .forms import SupplierForm, SupplierTransactionForm


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


def _can_access_suppliers(user):
    # Superusers, Managers and Supervisors may view suppliers and pay them.
    # Which account a payment may be drawn from is role-dependent — see
    # _payable_accounts_for (Supervisors are restricted to cash).
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )


def supplier_access_required(view_func):
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _can_access_suppliers(request.user):
            messages.error(request, 'You do not have permission to access supplier records.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def _payable_accounts_for(user):
    """Accounts a user may pay a supplier from.

    Superusers and Managers may use any liquid account (cash / M-Pesa / bank);
    Supervisors are restricted to the cash account only. Receivable (`_ar`)
    accounts are never a payment source.
    """
    from administration.models import Account
    qs = Account.objects.filter(is_active=True).exclude(
        account_type__endswith=Account.RECEIVABLE_SUFFIX,
    )
    if not (user.is_superuser or user.groups.filter(name='Manager').exists()):
        qs = qs.filter(account_type='cash')
    return qs.order_by('name')


# ── Supplier list ────────────────────────────────────────────────────

@supplier_access_required
def supplier_list(request):
    show = request.GET.get('show', 'active')
    if show == 'all':
        suppliers = Supplier.objects.all()
    else:
        suppliers = Supplier.objects.filter(is_active=True)
    return render(request, 'supplier/supplier_list.html', {
        'suppliers': suppliers,
        'show': show,
    })


# ── Supplier create / edit ───────────────────────────────────────────

@superuser_only
def supplier_create(request):
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Supplier created.')
            return redirect('supplier-list')
    else:
        form = SupplierForm()
    return render(request, 'supplier/supplier_form.html', {
        'form': form, 'title': 'Add Supplier',
    })


@superuser_only
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, f'{supplier.name} updated.')
            return redirect('supplier-detail', pk=supplier.pk)
    else:
        form = SupplierForm(instance=supplier)
    return render(request, 'supplier/supplier_form.html', {
        'form': form, 'title': f'Edit {supplier.name}',
    })


# ── Supplier detail (account ledger) ────────────────────────────────

@supplier_access_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    transactions = supplier.transactions.all()

    txn_type = request.GET.get('type')
    if txn_type in ('debit', 'credit'):
        transactions = transactions.filter(transaction_type=txn_type)

    # Unpaid invoices for the summary section
    all_invoices = supplier.transactions.filter(transaction_type='debit')
    unpaid_invoices = [inv for inv in all_invoices if inv.remaining > Decimal('0')]

    return render(request, 'supplier/supplier_detail.html', {
        'supplier': supplier,
        'transactions': transactions,
        'txn_type': txn_type,
        'unpaid_invoices': unpaid_invoices,
    })


# ── Record invoice (debit only — payments go through make_payment) ──

@superuser_only
def transaction_create(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        form = SupplierTransactionForm(request.POST)
        if form.is_valid():
            txn = form.save(commit=False)
            txn.supplier = supplier
            txn.transaction_type = 'debit'
            txn.created_by = request.user
            txn.save()

            from menu.cache import get_restaurant_settings
            symbol = get_restaurant_settings().currency_symbol
            messages.success(request, f'Invoice recorded — {symbol} {txn.amount:,.2f}')
            return redirect('supplier-detail', pk=supplier.pk)
    else:
        form = SupplierTransactionForm()
    return render(request, 'supplier/transaction_form.html', {
        'form': form,
        'supplier': supplier,
    })


# ── Make payment against invoices ────────────────────────────────────

@supplier_access_required
def make_payment(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)

    # Get unpaid invoices (debit transactions with remaining > 0)
    all_invoices = supplier.transactions.filter(transaction_type='debit')
    unpaid_invoices = [inv for inv in all_invoices if inv.remaining > Decimal('0')]

    # Accounts this user is allowed to pay from (supervisors → cash only)
    payable_accounts = _payable_accounts_for(request.user)

    if request.method == 'POST':
        payment_amount = request.POST.get('payment_amount', '0')
        payment_note = request.POST.get('payment_note', '').strip()
        account_id = request.POST.get('account_id', '')
        try:
            payment_amount = Decimal(payment_amount).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid payment amount.')
            return redirect('supplier-make-payment', pk=supplier.pk)

        if payment_amount <= 0:
            messages.error(request, 'Payment amount must be greater than zero.')
            return redirect('supplier-make-payment', pk=supplier.pk)

        # The chosen account must be one this user is permitted to pay from.
        pay_account = (
            payable_accounts.filter(pk=account_id).first()
            if account_id.isdigit() else None
        )
        if pay_account is None:
            messages.error(request, 'Please choose a valid account to pay from.')
            return redirect('supplier-make-payment', pk=supplier.pk)

        # Don't allow paying more than the account holds.
        if pay_account.balance < payment_amount:
            from menu.cache import get_restaurant_settings
            symbol = get_restaurant_settings().currency_symbol
            messages.error(
                request,
                f'Insufficient balance in {pay_account.name} — available '
                f'{symbol} {pay_account.balance:,.2f}, payment '
                f'{symbol} {payment_amount:,.2f}.',
            )
            return redirect('supplier-make-payment', pk=supplier.pk)

        with transaction.atomic():
            # Create the payment (credit) transaction
            payment_txn = SupplierTransaction.objects.create(
                supplier=supplier,
                transaction_type='credit',
                amount=payment_amount,
                description=payment_note or f'Payment to {supplier.name}',
                created_by=request.user,
            )

            # Allocate payment against invoices (oldest first)
            remaining_payment = payment_amount
            for inv in sorted(unpaid_invoices, key=lambda x: x.date):
                if remaining_payment <= 0:
                    break
                inv_remaining = inv.remaining
                allocated = min(remaining_payment, inv_remaining)
                SupplierPaymentAllocation.objects.create(
                    payment=payment_txn,
                    invoice=inv,
                    amount=allocated,
                )
                inv.amount_paid += allocated
                inv.save()
                remaining_payment -= allocated

            # Debit the chosen account
            from administration.models import Transaction as AcctTransaction
            AcctTransaction.objects.create(
                account=pay_account,
                transaction_type='debit',
                amount=payment_amount,
                description=f'Supplier payment — {supplier.name}',
                reference_type='supplier_payment',
                reference_id=payment_txn.id,
                created_by=request.user,
            )

        from menu.cache import get_restaurant_settings
        symbol = get_restaurant_settings().currency_symbol
        messages.success(
            request,
            f'Payment of {symbol} {payment_amount:,.2f} to {supplier.name} '
            f'recorded from {pay_account.name}.',
        )
        return redirect('supplier-detail', pk=supplier.pk)

    from menu.cache import get_restaurant_settings
    symbol = get_restaurant_settings().currency_symbol

    account_list = [
        {
            'pk': a.pk,
            'name': a.name,
            'account_type': a.account_type,
            'balance': a.balance,
        }
        for a in payable_accounts
    ]

    context = {
        'supplier': supplier,
        'unpaid_invoices': unpaid_invoices,
        'total_outstanding': sum(inv.remaining for inv in unpaid_invoices),
        'currency_symbol': symbol,
        'account_list': account_list,
    }
    return render(request, 'supplier/make_payment.html', context)
