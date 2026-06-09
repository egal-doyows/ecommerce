from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from menu.models import InventoryItem, MenuItem


class StaffMealLog(models.Model):
    """
    A staff-meal event — tea, lunch, etc. consumed by staff.

    Recorded separately from sales (no revenue) and from waste (not shrinkage),
    so staff welfare is its own cost line. Each line item is either a prepared
    menu item (deducted via its recipe) or a raw inventory item (deducted
    directly). Stock is deducted when the log is created.
    """

    MEAL_TYPE_CHOICES = [
        ('tea', 'Tea / Break'),
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('other', 'Other'),
    ]

    meal_type = models.CharField(max_length=10, choices=MEAL_TYPE_CHOICES)
    date = models.DateField(default=timezone.now)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='staff_meal_logs',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"SM-{self.pk:04d} — {self.get_meal_type_display()}"

    @property
    def meal_number(self):
        return f"SM-{self.pk:04d}"

    @property
    def total_cost(self):
        return sum(item.cost for item in self.items.all())

    @property
    def item_count(self):
        return self.items.count()


class StaffMealItem(models.Model):
    """
    A single line on a staff-meal log. References exactly one of:
      - menu_item       → a prepared/sold dish, deducted via its recipe
      - inventory_item  → a raw stock item, deducted directly
    """

    staff_meal_log = models.ForeignKey(
        StaffMealLog, on_delete=models.CASCADE, related_name='items',
    )
    menu_item = models.ForeignKey(
        MenuItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_meal_items',
    )
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_meal_items',
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Cost per unit at time of consumption',
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(
                # Exactly one of menu_item / inventory_item must be set.
                check=(
                    models.Q(menu_item__isnull=False, inventory_item__isnull=True)
                    | models.Q(menu_item__isnull=True, inventory_item__isnull=False)
                ),
                name='staffmealitem_exactly_one_source',
            ),
        ]

    def clean(self):
        if bool(self.menu_item_id) == bool(self.inventory_item_id):
            raise ValidationError('Set exactly one of menu item or inventory item.')

    def __str__(self):
        return f"{self.item_name} — {self.quantity} {self.unit_label}"

    @property
    def item_name(self):
        if self.menu_item_id:
            return self.menu_item.title
        return self.inventory_item.name if self.inventory_item_id else '—'

    @property
    def unit_label(self):
        """Display unit — menu items are counted in servings."""
        if self.inventory_item_id:
            return self.inventory_item.get_unit_display()
        return 'serving'

    @property
    def is_menu_item(self):
        return self.menu_item_id is not None

    @property
    def cost(self):
        return (self.quantity or Decimal('0')) * (self.unit_cost or Decimal('0'))
