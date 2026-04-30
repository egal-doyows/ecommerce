from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction

from .models import Supplier, SupplierTransaction, SupplierPaymentAllocation
from .forms import SupplierForm, SupplierTransactionForm


def _is_manager(user):
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name='Manager').exists())


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


# ── Supplier list ────────────────────────────────────────────────────

@manager_required
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

@manager_required
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

            from menu.models import RestaurantSettings
            symbol = RestaurantSettings.load().currency_symbol
            messages.success(request, f'Invoice recorded — {symbol} {txn.amount:,.2f}')
            return redirect('supplier-detail', pk=supplier.pk)
    else:
        form = SupplierTransactionForm()
    return render(request, 'supplier/transaction_form.html', {
        'form': form,
        'supplier': supplier,
    })


# ── Make payment against invoices ────────────────────────────────────

@superuser_only
def make_payment(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)

    # Get unpaid invoices (debit transactions with remaining > 0)
    all_invoices = supplier.transactions.filter(transaction_type='debit')
    unpaid_invoices = [inv for inv in all_invoices if inv.remaining > Decimal('0')]

    if request.method == 'POST':
        payment_amount = request.POST.get('payment_amount', '0')
        payment_note = request.POST.get('payment_note', '').strip()
        try:
            payment_amount = Decimal(payment_amount).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid payment amount.')
            return redirect('supplier-make-payment', pk=supplier.pk)

        if payment_amount <= 0:
            messages.error(request, 'Payment amount must be greater than zero.')
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

            # Debit the cash account
            from administration.models import Account, Transaction as AcctTransaction
            cash_account = Account.get_by_type('cash')
            AcctTransaction.objects.create(
                account=cash_account,
                transaction_type='debit',
                amount=payment_amount,
                description=f'Supplier payment — {supplier.name}',
                reference_type='supplier_payment',
                reference_id=payment_txn.id,
                created_by=request.user,
            )

        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        messages.success(request, f'Payment of {symbol} {payment_amount:,.2f} to {supplier.name} recorded.')
        return redirect('supplier-detail', pk=supplier.pk)

    from menu.models import RestaurantSettings
    symbol = RestaurantSettings.load().currency_symbol

    context = {
        'supplier': supplier,
        'unpaid_invoices': unpaid_invoices,
        'total_outstanding': sum(inv.remaining for inv in unpaid_invoices),
        'currency_symbol': symbol,
    }
    return render(request, 'supplier/make_payment.html', context)
