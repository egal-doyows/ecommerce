from django.conf import settings
from django.db import models
from django.utils import timezone

from menu.models import InventoryItem


class StockMovement(models.Model):
    """Tracks every inventory movement — orders, receiving, adjustments, waste."""
    TYPE_CHOICES = [
        ('sale', 'Sale (Order)'),
        ('cancel', 'Order Cancelled'),
        ('received', 'Goods Received'),
        ('adjustment', 'Stock Adjustment'),
        ('waste', 'Waste'),
    ]

    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name='movements',
    )
    branch = models.ForeignKey(
        'branches.Branch', on_delete=models.CASCADE,
        null=True, blank=True, related_name='stock_movements',
    )
    movement_type = models.CharField(max_length=15, choices=TYPE_CHOICES)
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Positive = stock in, Negative = stock out',
    )
    balance_after = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Stock level after this movement',
    )
    reference = models.CharField(
        max_length=200, blank=True,
        help_text='Order number, PO number, adjustment reason, etc.',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='stock_movements',
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['inventory_item', '-created_at']),
            models.Index(fields=['branch', 'movement_type', '-created_at']),
        ]

    def __str__(self):
        sign = '+' if self.quantity > 0 else ''
        return f"{self.inventory_item.name}: {sign}{self.quantity} ({self.get_movement_type_display()})"


class StockAdjustment(models.Model):
    """Batch stock adjustment — e.g. stocktake results, corrections."""
    REASON_CHOICES = [
        ('stocktake', 'Stocktake'),
        ('damage', 'Damaged Goods'),
        ('expired', 'Expired'),
        ('correction', 'Correction'),
        ('other', 'Other'),
    ]

    branch = models.ForeignKey(
        'branches.Branch', on_delete=models.CASCADE,
        null=True, blank=True, related_name='stock_adjustments',
    )
    reason = models.CharField(max_length=15, choices=REASON_CHOICES)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='stock_adjustments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Adjustment #{self.pk} — {self.get_reason_display()} ({self.created_at.strftime('%d %b %Y')})"


class StockAdjustmentLine(models.Model):
    """Individual item within a stock adjustment."""
    adjustment = models.ForeignKey(
        StockAdjustment, on_delete=models.CASCADE, related_name='lines',
    )
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE,
    )
    old_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    new_quantity = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def difference(self):
        return self.new_quantity - self.old_quantity

    def __str__(self):
        diff = self.difference
        sign = '+' if diff > 0 else ''
        return f"{self.inventory_item.name}: {sign}{diff}"
