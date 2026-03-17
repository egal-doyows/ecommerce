from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from menu.models import InventoryItem


class WasteLog(models.Model):
    """
    A waste event recorded against inventory items.
    Tracks what was wasted, why, and by whom.
    Stock is automatically deducted when waste is logged.
    """

    REASON_CHOICES = [
        ('expired', 'Expired'),
        ('spoiled', 'Spoiled / Rotten'),
        ('overprep', 'Over-preparation'),
        ('spillage', 'Spillage'),
        ('returned', 'Customer Return'),
        ('damaged', 'Damaged'),
        ('theft', 'Theft / Missing'),
        ('other', 'Other'),
    ]

    reason = models.CharField(max_length=10, choices=REASON_CHOICES)
    date = models.DateField(default=timezone.now)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='waste_logs',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"WST-{self.pk:04d} — {self.get_reason_display()}"

    @property
    def waste_number(self):
        return f"WST-{self.pk:04d}"

    @property
    def total_cost(self):
        return sum(item.cost for item in self.items.all())

    @property
    def item_count(self):
        return self.items.count()


class WasteItem(models.Model):
    """Line item on a waste log — a single inventory item that was wasted."""

    waste_log = models.ForeignKey(
        WasteLog, on_delete=models.CASCADE, related_name='items',
    )
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name='waste_items',
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Cost per unit at time of waste',
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f"{self.inventory_item.name} — {self.quantity} {self.inventory_item.get_unit_display()}"

    @property
    def cost(self):
        return self.quantity * self.unit_cost
