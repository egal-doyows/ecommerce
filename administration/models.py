from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Account(models.Model):
    """
    Financial accounts that receive or disburse money.

    Three built-in accounts (auto-created via setup_accounts command):
      - cash  → Cash Register
      - mpesa → M-Pesa Till
      - bank  → Bank Account
    """

    ACCOUNT_TYPE_CHOICES = [
        ('cash', 'Cash Register'),
        ('mpesa', 'M-Pesa Till'),
        ('bank', 'Bank Account'),
    ]

    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, null=True, blank=True, related_name='accounts')
    name = models.CharField(max_length=100)
    account_type = models.CharField(
        max_length=10, choices=ACCOUNT_TYPE_CHOICES,
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = ('branch', 'account_type')

    def __str__(self):
        return self.name

    @property
    def balance(self):
        """Current balance = total credits - total debits."""
        agg = self.transactions.aggregate(
            credits=models.Sum(
                'amount', filter=models.Q(transaction_type='credit'),
            ),
            debits=models.Sum(
                'amount', filter=models.Q(transaction_type='debit'),
            ),
        )
        credits = agg['credits'] or Decimal('0')
        debits = agg['debits'] or Decimal('0')
        return credits - debits

    def balance_as_of(self, end_date):
        """Balance up to a given datetime."""
        qs = self.transactions.filter(created_at__lte=end_date)
        agg = qs.aggregate(
            credits=models.Sum(
                'amount', filter=models.Q(transaction_type='credit'),
            ),
            debits=models.Sum(
                'amount', filter=models.Q(transaction_type='debit'),
            ),
        )
        credits = agg['credits'] or Decimal('0')
        debits = agg['debits'] or Decimal('0')
        return credits - debits

    @classmethod
    def get_by_type(cls, account_type, branch=None):
        """Return the account for a given type and branch, creating it if needed."""
        defaults = dict(cls.ACCOUNT_TYPE_CHOICES)
        account, _ = cls.objects.get_or_create(
            account_type=account_type,
            branch=branch,
            defaults={'name': defaults.get(account_type, account_type)},
        )
        return account


class Transaction(models.Model):
    """
    A single credit or debit against an Account.

    reference_type / reference_id point back to the source:
      - 'order'          → menu.Order.pk
      - 'staff_payment'  → staff_compensation.PaymentRecord.pk
      - 'manual'         → manual adjustment
    """

    TRANSACTION_TYPE_CHOICES = [
        ('credit', 'Credit'),
        ('debit', 'Debit'),
    ]

    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, null=True, blank=True, related_name='transactions')
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name='transactions',
    )
    transaction_type = models.CharField(
        max_length=6, choices=TRANSACTION_TYPE_CHOICES,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=250)
    reference_type = models.CharField(
        max_length=20, blank=True,
        help_text="e.g. order, staff_payment, manual",
    )
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='account_transactions',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['branch', 'created_at']),
            models.Index(fields=['account', 'transaction_type', 'created_at']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        sign = '+' if self.transaction_type == 'credit' else '-'
        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        return f"{self.account.name} {sign}{symbol} {self.amount} — {self.description}"


# ── Helper functions called from other apps ──────────────────────────

ORDER_PAYMENT_TO_ACCOUNT = {
    'cash': 'cash',
    'mpesa': 'mpesa',
    'card': 'bank',
}


def record_order_payment(order, created_by=None):
    """
    Create a credit transaction when an order is paid.
    Called from menu.views.order_update_status.
    """
    account_type = ORDER_PAYMENT_TO_ACCOUNT.get(order.payment_method)
    if not account_type:
        return None

    account = Account.get_by_type(account_type, branch=order.branch)
    return Transaction.objects.create(
        account=account,
        branch=order.branch,
        transaction_type='credit',
        amount=order.get_total(),
        description=f'Order #{order.id} — Space {order.table.number if order.table else "N/A"}',
        reference_type='order',
        reference_id=order.id,
        created_by=created_by,
    )


STAFF_PAYMENT_TO_ACCOUNT = {
    'cash': 'cash',
    'mpesa': 'mpesa',
    'bank': 'bank',
}


def record_staff_payment(payment_record, account=None, created_by=None, amount=None):
    """
    Create a debit transaction when a staff member is paid.
    Called from staff_compensation.views.pay_staff.
    If account is provided, use it directly; otherwise derive from disbursement_method.
    If amount is provided, use it (for partial payments); otherwise use payment_record.amount.
    """
    if account is None:
        account_type = STAFF_PAYMENT_TO_ACCOUNT.get(payment_record.disbursement_method)
        if not account_type:
            return None
        account = Account.get_by_type(account_type, branch=payment_record.branch)

    pay_amount = amount if amount is not None else payment_record.amount

    return Transaction.objects.create(
        account=account,
        branch=payment_record.branch,
        transaction_type='debit',
        amount=pay_amount,
        description=f'Staff payment — {payment_record.staff.username} ({payment_record.month_label})',
        reference_type='staff_payment',
        reference_id=payment_record.id,
        created_by=created_by,
    )
