from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class ExpenseCategory(models.Model):
    """
    Categories for organising expenses.
    Pre-populated with common restaurant categories; users can add more.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Expense Categories'

    def __str__(self):
        return self.name

    @property
    def total_spent(self):
        agg = self.expenses.aggregate(t=models.Sum('amount'))
        return agg['t'] or Decimal('0')


class Expense(models.Model):
    """
    A single expense record.
    When saved, a debit Transaction is created against the chosen payment account
    to keep the books balanced.
    """

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('mpesa', 'M-Pesa'),
        ('bank', 'Bank Transfer'),
    ]

    RECURRING_CHOICES = [
        ('none', 'One-time'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.SET_NULL, null=True,
        related_name='expenses',
    )
    description = models.CharField(max_length=250)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=timezone.now)
    payment_method = models.CharField(
        max_length=10, choices=PAYMENT_METHOD_CHOICES, default='cash',
    )
    receipt_number = models.CharField(max_length=100, blank=True,
                                      help_text='Invoice or receipt reference')
    vendor = models.CharField(max_length=200, blank=True,
                              help_text='Who was paid')
    recurring = models.CharField(max_length=10, choices=RECURRING_CHOICES,
                                 default='none')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='recorded_expenses',
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_expenses',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"EXP-{self.pk:04d} — {self.description}"

    @property
    def expense_number(self):
        return f"EXP-{self.pk:04d}"
