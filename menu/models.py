from decimal import Decimal

from django.db import models, transaction
from django.urls import reverse
from django.contrib.auth.models import User


class RestaurantSettings(models.Model):
    name = models.CharField(max_length=150, default='RestoPOS')
    tagline = models.CharField(max_length=250, blank=True, default='Your Favourite Restaurant')
    phone = models.CharField(max_length=30, blank=True)
    website = models.CharField(max_length=150, blank=True)
    logo = models.ImageField(upload_to='restaurant/', blank=True)
    currency = models.CharField(
        max_length=3,
        default='KES',
        help_text='Currency code (e.g. KES, USD, EUR)',
    )
    default_markup_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('50'),
        help_text='Default markup applied to cost when suggesting menu prices',
    )

    class Meta:
        verbose_name = 'Restaurant Settings'
        verbose_name_plural = 'Restaurant Settings'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Ensure only one instance exists
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def currency_symbol(self):
        from .currencies import CURRENCY_SYMBOLS
        return CURRENCY_SYMBOLS.get(self.currency, self.currency)


class Category(models.Model):
    name = models.CharField(max_length=250, db_index=True)
    slug = models.SlugField(max_length=250, unique=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Font Awesome icon class, e.g. fa-coffee")

    class Meta:
        verbose_name_plural = 'categories'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('category-filter', args=[self.slug])


class InventoryItem(models.Model):
    """
    Physical stock item. Two use cases:
      1. Direct sale  — sold as-is on the menu (soda, water, beer).
      2. Ingredient   — consumed when a prepared menu item is made (mango, flour, oil).
    An item can be both (e.g. milk sold by the glass AND used in coffee).
    """

    UNIT_CHOICES = [
        ('kg', 'Kilograms'),
        ('g', 'Grams'),
        ('l', 'Litres'),
        ('ml', 'Millilitres'),
        ('bottle', 'Bottles'),
        ('piece', 'Pieces'),
        ('packet', 'Packets'),
        ('box', 'Boxes'),
        ('bunch', 'Bunches'),
    ]

    name = models.CharField(max_length=250)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='piece')
    stock_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    buying_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Cost per unit for profit tracking",
    )
    low_stock_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, default=5,
        help_text="Alert when stock falls to this level",
    )
    preferred_supplier = models.ForeignKey(
        'supplier.Supplier', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='inventory_items',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.stock_quantity} {self.get_unit_display()})"

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold

    def deduct(self, quantity):
        """Deduct stock using F() to avoid race conditions. Returns True on success."""
        quantity = Decimal(str(quantity))
        updated = InventoryItem.objects.filter(
            pk=self.pk, stock_quantity__gte=quantity,
        ).update(stock_quantity=models.F('stock_quantity') - quantity)
        if updated:
            self.refresh_from_db(fields=['stock_quantity'])
            return True
        return False

    def restore(self, quantity):
        """Add stock back (e.g. cancelled order)."""
        quantity = Decimal(str(quantity))
        InventoryItem.objects.filter(pk=self.pk).update(
            stock_quantity=models.F('stock_quantity') + quantity,
        )
        self.refresh_from_db(fields=['stock_quantity'])


class MenuItem(models.Model):
    """
    Everything the customer can order.

    Stock behaviour is determined by how you configure it:
      - Set `inventory_item` → direct sale (1 sold = 1 deducted from inventory).
      - Add Recipe rows   → prepared item (ingredients deducted per recipe).
      - Neither           → untracked item (no stock impact).
    """

    TIER_CHOICES = [
        ('regular', 'Regular'),
        ('premium', 'Premium'),
    ]

    category = models.ForeignKey(Category, related_name='items', on_delete=models.CASCADE)
    title = models.CharField(max_length=250)
    slug = models.SlugField(max_length=250)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=2)
    image = models.ImageField(upload_to='images/', blank=True)
    is_available = models.BooleanField(default=True)
    item_tier = models.CharField(
        max_length=10, choices=TIER_CHOICES, default='regular',
        help_text="Regular or Premium — affects staff commission eligibility",
    )
    preparation_time = models.PositiveIntegerField(default=10, help_text="Estimated prep time in minutes")
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='menu_items',
        help_text="For direct-sale items (soda, water). Leave blank for prepared food.",
    )

    class Meta:
        verbose_name_plural = 'menu items'
        ordering = ['category', 'title']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('item-detail', args=[self.slug])

    @property
    def is_direct_sale(self):
        return self.inventory_item_id is not None

    @property
    def tracks_stock(self):
        return self.is_direct_sale or self.recipe_items.exists()

    @transaction.atomic
    def deduct_stock(self, quantity=1):
        """
        Deduct inventory when this item is ordered.
        Atomic — either everything deducts or nothing does.
        Returns True on success, False if any ingredient is insufficient.
        """
        if self.is_direct_sale:
            return self.inventory_item.deduct(quantity)

        ingredients = self.recipe_items.select_related('inventory_item').all()
        if not ingredients:
            return True  # untracked item

        # Check all ingredients first, then deduct
        for ingredient in ingredients:
            needed = ingredient.quantity_required * Decimal(str(quantity))
            if not ingredient.inventory_item.deduct(needed):
                raise _InsufficientStock(ingredient.inventory_item.name)
        return True

    @transaction.atomic
    def restore_stock(self, quantity=1):
        """Restore inventory when an order is cancelled."""
        if self.is_direct_sale:
            self.inventory_item.restore(quantity)
            return

        for ingredient in self.recipe_items.select_related('inventory_item').all():
            needed = ingredient.quantity_required * Decimal(str(quantity))
            ingredient.inventory_item.restore(needed)

    def current_unit_cost(self):
        """
        Cost-of-goods for one unit, computed from current inventory buying_prices.
        Returns Decimal('0') for untracked items (no inventory_item, no recipe).
        Snapshot this at order time — buying_price drifts on every receipt.
        """
        if self.is_direct_sale:
            return self.inventory_item.buying_price
        cost = Decimal('0')
        for r in self.recipe_items.select_related('inventory_item').all():
            cost += r.quantity_required * r.inventory_item.buying_price
        return cost

    def suggested_price(self, markup_percent):
        """
        Suggested selling price = cost × (1 + markup%/100), rounded to 2dp.
        Returns None when cost is 0 (untracked item — nothing to mark up).
        """
        cost = self.current_unit_cost()
        if cost <= 0:
            return None
        multiplier = Decimal('1') + (Decimal(str(markup_percent)) / Decimal('100'))
        return (cost * multiplier).quantize(Decimal('0.01'))


class _InsufficientStock(Exception):
    """Raised inside atomic block to trigger rollback."""
    pass


class StockAdjustment(models.Model):
    """
    Audit trail for explicit stock changes outside the normal sale/restock flow.
    Each row records the delta and who made it; InventoryItem.stock_quantity is
    the running total it should equal when summed.
    """

    SOURCE_CHOICES = [
        ('count', 'Physical count'),
        ('manual', 'Manual adjustment'),
        ('write_off', 'Write-off'),
    ]

    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name='stock_adjustments',
    )
    qty_delta = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Positive = added, negative = removed',
    )
    reason = models.CharField(max_length=250, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='count')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_adjustments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.qty_delta >= 0 else ''
        return f'{self.inventory_item.name} {sign}{self.qty_delta} ({self.get_source_display()})'


class Recipe(models.Model):
    """
    Links a prepared MenuItem to the InventoryItems it consumes.

    Example: Mango Juice recipe
      - 1 piece  Mango
      - 0.05 kg  Sugar
      - 0.2 l    Water
    """

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='recipe_items')
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='used_in_recipes')
    quantity_required = models.DecimalField(
        max_digits=8, decimal_places=3,
        help_text="Amount of this ingredient consumed per 1 menu item sold",
    )

    class Meta:
        unique_together = ('menu_item', 'inventory_item')
        verbose_name_plural = 'recipes'

    def __str__(self):
        return (
            f"{self.menu_item.title} — "
            f"{self.quantity_required} {self.inventory_item.get_unit_display()} "
            f"{self.inventory_item.name}"
        )


class Table(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('reserved', 'Reserved'),
    ]

    number = models.PositiveIntegerField(unique=True)
    capacity = models.PositiveIntegerField(default=4)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='available')

    class Meta:
        ordering = ['number']

    def __str__(self):
        return f"Space {self.number}"


class Shift(models.Model):
    waiter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shifts')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    starting_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    counted_cash = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Cash actually counted in the drawer at shift close",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Shift #{self.id} — {self.waiter.username} ({self.started_at.strftime('%d %b %H:%M')})"

    def get_duration(self):
        from django.utils import timezone
        end = self.ended_at or timezone.now()
        delta = end - self.started_at
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m"

    def get_orders(self):
        return self.orders.all()

    def get_order_count(self):
        return self.orders.count()

    def get_total_sales(self):
        return sum(order.get_total() for order in self.orders.filter(status='paid'))

    def get_total_items(self):
        return sum(order.get_item_count() for order in self.orders.all())


class Order(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ]

    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('mpesa', 'M-Pesa'),
        ('card', 'Card'),
        ('credit', 'Credit (Debtor)'),
    ]

    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, related_name='orders')
    waiter = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='orders')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_orders',
        help_text="Marketing staff who created this order (earns commission alongside the attendant)",
    )
    debtor = models.ForeignKey(
        'debtor.Debtor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders',
    )
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, blank=True)
    mpesa_code = models.CharField(max_length=4, blank=True, help_text="Last 4 characters of M-Pesa transaction code")
    discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Manager-authorised discount on this order, in base currency",
    )
    is_comp = models.BooleanField(
        default=False,
        help_text="Order was comped (free) — counted as a loss-prevention event, not revenue",
    )
    authorized_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='authorised_orders',
        help_text="Manager who approved a void/discount/comp on this order",
    )
    authorization_reason = models.TextField(
        blank=True,
        help_text="Reason given when the order was voided, discounted, or comped",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} - Space {self.table.number if self.table else 'N/A'}"

    def delete(self, *args, **kwargs):
        table = self.table
        super().delete(*args, **kwargs)
        if table and not table.orders.filter(status='active').exists():
            table.status = 'available'
            table.save()

    def get_total(self):
        return sum(item.get_subtotal() for item in self.items.all())

    def get_item_count(self):
        return sum(item.quantity for item in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        help_text="COGS per unit, snapshotted at order time",
    )
    notes = models.CharField(max_length=250, blank=True, help_text="Special requests")

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.title}"

    def get_subtotal(self):
        return self.unit_price * self.quantity

    def get_cost_subtotal(self):
        return self.unit_cost * self.quantity
