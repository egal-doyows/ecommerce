from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from administration.models import Account, Transaction as AccountTransaction

from .models import DebtorPaymentAllocation, DebtorTransaction


PAYMENT_ACCOUNT_TYPES = {
    'cash': 'cash',
    'mpesa': 'mpesa',
    'card': 'bank',
}


def validate_payment_details(method, reference=''):
    reference = (reference or '').strip().upper()
    if method not in PAYMENT_ACCOUNT_TYPES:
        raise ValidationError('Choose cash, M-Pesa, or card.')
    if method == 'mpesa' and (len(reference) != 4 or not reference.isalnum()):
        raise ValidationError('Enter the last 4 characters of the M-Pesa code.')
    return reference[:50]


def record_debtor_payment(*, debtor, amount, method, created_by, reference='',
                          note='', invoice=None):
    """Record and allocate a debtor payment, routing it to the proper account.

    If ``invoice`` is supplied, allocation is limited to that invoice. Otherwise
    it is applied oldest-first across all of the debtor's open invoices.
    """
    amount = Decimal(amount).quantize(Decimal('0.01'))
    reference = validate_payment_details(method, reference)
    if amount <= 0:
        raise ValidationError('Payment amount must be greater than zero.')

    with transaction.atomic():
        invoices = DebtorTransaction.objects.select_for_update().filter(
            debtor=debtor, transaction_type='debit',
        )
        if invoice is not None:
            invoices = invoices.filter(pk=invoice.pk)
        locked_invoices = list(invoices.order_by('date', 'id'))
        outstanding = sum((item.remaining for item in locked_invoices), Decimal('0'))
        if outstanding <= 0:
            raise ValidationError('This invoice is already fully paid.' if invoice else 'This debtor has no outstanding invoices.')
        if amount > outstanding:
            raise ValidationError(f'Payment cannot exceed the outstanding amount ({outstanding:.2f}).')

        payment = DebtorTransaction.objects.create(
            debtor=debtor,
            transaction_type='credit',
            amount=amount,
            payment_method=method,
            payment_reference=reference,
            description=note or f'Payment received from {debtor.name}',
            reference=str(invoice.order_id) if invoice is not None and invoice.order_id else '',
            created_by=created_by,
        )

        unallocated = amount
        for item in locked_invoices:
            if unallocated <= 0:
                break
            allocated = min(unallocated, item.remaining)
            if allocated <= 0:
                continue
            DebtorPaymentAllocation.objects.create(
                payment=payment, invoice=item, amount=allocated,
            )
            item.amount_paid += allocated
            item.save(update_fields=['amount_paid'])
            unallocated -= allocated

        account = Account.get_by_type(PAYMENT_ACCOUNT_TYPES[method])
        AccountTransaction.objects.create(
            account=account,
            transaction_type='credit',
            amount=amount,
            description=f'Debtor payment — {debtor.name}',
            reference_type='debtor_payment',
            reference_id=payment.id,
            created_by=created_by,
        )
        return payment


def reverse_debtor_payment(*, payment, created_by):
    """Reverse an allocated payment and its account impact, then remove it."""
    with transaction.atomic():
        payment = DebtorTransaction.objects.select_for_update().get(
            pk=payment.pk, transaction_type='credit',
        )
        allocations = list(
            payment.allocations.select_related('invoice').select_for_update()
        )
        for allocation in allocations:
            invoice = allocation.invoice
            invoice.amount_paid = max(Decimal('0'), invoice.amount_paid - allocation.amount)
            invoice.save(update_fields=['amount_paid'])

        account_entries = list(AccountTransaction.objects.filter(
            reference_type='debtor_payment', reference_id=payment.id,
            transaction_type='credit',
        ))
        for entry in account_entries:
            AccountTransaction.objects.create(
                account=entry.account,
                transaction_type='debit',
                amount=entry.amount,
                description=f'Reversal — {entry.description}',
                reference_type='debtor_payment_reversal',
                reference_id=payment.id,
                created_by=created_by,
            )
        payment.delete()
