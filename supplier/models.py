from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Supplier(models.Model):
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def balance(self):
        """Positive = we owe the supplier. Negative = overpaid/credit."""
        agg = self.transactions.aggregate(
            debits=models.Sum(
                'amount', filter=models.Q(transaction_type='debit'),
            ),
            credits=models.Sum(
                'amount', filter=models.Q(transaction_type='credit'),
            ),
        )
        debits = agg['debits'] or Decimal('0')   # invoices / goods received
        credits = agg['credits'] or Decimal('0')  # payments made
        return debits - credits

    @property
    def total_invoiced(self):
        return self.transactions.filter(
            transaction_type='debit',
        ).aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    @property
    def total_paid(self):
        return self.transactions.filter(
            transaction_type='credit',
        ).aggregate(t=models.Sum('amount'))['t'] or Decimal('0')


class SupplierTransaction(models.Model):
    """
    Tracks money flow with a supplier.

    debit  = we owe more (invoice / goods received)
    credit = we paid them (payment, linked to invoices)
    """

    TRANSACTION_TYPE_CHOICES = [
        ('debit', 'Invoice / Goods Received'),
        ('credit', 'Payment'),
    ]

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name='transactions',
    )
    transaction_type = models.CharField(
        max_length=6, choices=TRANSACTION_TYPE_CHOICES,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="How much of this invoice has been paid (debit transactions only)",
    )
    description = models.CharField(max_length=250)
    reference = models.CharField(
        max_length=100, blank=True,
        help_text="Invoice number, receipt number, etc.",
    )
    date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supplier_transactions',
    )

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        sign = '+' if self.transaction_type == 'debit' else '-'
        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        return f"{self.supplier.name} {sign}{symbol} {self.amount} — {self.description}"

    @property
    def remaining(self):
        """Amount still unpaid on this invoice."""
        if self.transaction_type != 'debit':
            return Decimal('0')
        return self.amount - self.amount_paid

    @property
    def is_fully_paid(self):
        return self.transaction_type == 'debit' and self.amount_paid >= self.amount


class SupplierPaymentAllocation(models.Model):
    """
    Links a payment (credit transaction) to the invoices (debit transactions) it covers.
    A single payment can be split across multiple invoices.
    """
    payment = models.ForeignKey(
        SupplierTransaction, on_delete=models.CASCADE, related_name='allocations',
        limit_choices_to={'transaction_type': 'credit'},
    )
    invoice = models.ForeignKey(
        SupplierTransaction, on_delete=models.CASCADE, related_name='payment_allocations',
        limit_choices_to={'transaction_type': 'debit'},
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('payment', 'invoice')

    def __str__(self):
        return f"Payment #{self.payment_id} → Invoice #{self.invoice_id}: {self.amount}"
