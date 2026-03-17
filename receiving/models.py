from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from purchasing.models import PurchaseOrder, PurchaseOrderItem


class GoodsReceipt(models.Model):
    """
    A goods receipt note (GRN) recorded against an approved purchase order.
    Supports partial receiving — a PO can have multiple receipts.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='receipts',
    )
    received_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='goods_receipts',
    )
    received_date = models.DateField(default=timezone.now)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"GRN-{self.pk:04d} — {self.purchase_order.po_number}"

    @property
    def grn_number(self):
        return f"GRN-{self.pk:04d}"

    @property
    def total_value(self):
        return sum(item.received_value for item in self.items.all())

    @property
    def item_count(self):
        return self.items.count()

    @property
    def has_discrepancy(self):
        """True if any item received differs from ordered quantity."""
        return any(
            i.received_quantity != i.po_item.quantity
            for i in self.items.select_related('po_item').all()
        )


class GoodsReceiptItem(models.Model):
    """Line item on a goods receipt — how much was actually received."""

    receipt = models.ForeignKey(
        GoodsReceipt, on_delete=models.CASCADE, related_name='items',
    )
    po_item = models.ForeignKey(
        PurchaseOrderItem, on_delete=models.CASCADE, related_name='receipt_items',
    )
    received_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True, help_text='Note any discrepancies or damage')

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f"{self.po_item.inventory_item.name} — received {self.received_quantity}"

    @property
    def received_value(self):
        return self.received_quantity * self.po_item.unit_price

    @property
    def ordered_quantity(self):
        return self.po_item.quantity
