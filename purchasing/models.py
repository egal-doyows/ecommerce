from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from supplier.models import Supplier
from menu.models import InventoryItem


class PurchaseOrder(models.Model):
    """
    A purchase order sent to a supplier for inventory items.
    Workflow: draft → approved → received → (optionally) cancelled at any stage.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('received', 'Received'),
        ('cancelled', 'Cancelled'),
    ]

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name='purchase_orders',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    order_date = models.DateField(default=timezone.now)
    expected_date = models.DateField(null=True, blank=True)
    received_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_orders_created',
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_orders_approved',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"PO-{self.pk:04d} — {self.supplier.name}"

    @property
    def po_number(self):
        return f"PO-{self.pk:04d}"

    @property
    def total(self):
        return sum(item.line_total for item in self.items.all())

    @property
    def item_count(self):
        return self.items.count()


class PurchaseOrderItem(models.Model):
    """Line item on a purchase order."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items',
    )
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name='purchase_order_items',
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f"{self.inventory_item.name} x {self.quantity}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    @property
    def received_quantity(self):
        """Total quantity received, derived from the goods-receipt (GRN) rows,
        which are the single source of truth. Previously this was a stored
        field updated in lockstep with the GRN rows, which could drift (e.g. a
        GRN deleted directly in admin). Sums in Python when ``receipt_items``
        is prefetched, otherwise aggregates in the DB."""
        cache = getattr(self, '_prefetched_objects_cache', None) or {}
        if 'receipt_items' in cache:
            return sum(
                (gri.received_quantity for gri in self.receipt_items.all()),
                Decimal('0'),
            )
        return self.receipt_items.aggregate(
            total=models.Sum('received_quantity'),
        )['total'] or Decimal('0')

    @property
    def received_total(self):
        return self.received_quantity * self.unit_price
