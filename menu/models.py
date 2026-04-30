from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.urls import reverse
from django.contrib.auth.models import User

from core.models import SoftDeleteModel, BranchScopedManager


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


class Station(models.Model):
    """Preparation station — e.g. Kitchen, Bar. Used to route order items
    to the correct display screen."""
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=250, db_index=True)
    slug = models.SlugField(max_length=250, unique=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Font Awesome icon class, e.g. fa-coffee")
    station = models.ForeignKey(
        Station, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='categories',
        help_text="Which preparation station handles items in this category",
    )

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

    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, null=True, blank=True, related_name='inventory_items')
    name = models.CharField(max_length=250)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='piece')
    stock_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(Decimal('0'))])
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

    objects = BranchScopedManager()

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['branch', 'name']),
        ]

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
    price = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
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
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['category', 'is_available']),
        ]

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
        Raises _InsufficientStock if any ingredient/item has insufficient stock.
        Returns True on success for untracked items.
        """
        if self.is_direct_sale:
            if not self.inventory_item.deduct(quantity):
                raise _InsufficientStock(self.inventory_item.name)
            return True

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


class _InsufficientStock(Exception):
    """Raised inside atomic block to trigger rollback."""
    pass


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

    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, null=True, blank=True, related_name='tables')
    number = models.PositiveIntegerField()
    capacity = models.PositiveIntegerField(default=4)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='available')

    class Meta:
        ordering = ['number']
        unique_together = ('branch', 'number')

    def __str__(self):
        return f"Space {self.number}"


class Shift(models.Model):
    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, null=True, blank=True, related_name='shifts')
    waiter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shifts')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    starting_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    objects = BranchScopedManager()

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['waiter', 'is_active', 'branch']),
            models.Index(fields=['branch', 'is_active']),
        ]

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
        from django.db.models import Sum, F
        result = self.orders.filter(status='paid').aggregate(
            total=Sum(F('items__unit_price') * F('items__quantity'))
        )['total']
        return result or 0

    def get_total_items(self):
        from django.db.models import Sum
        return self.orders.aggregate(total=Sum('items__quantity'))['total'] or 0


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

    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, null=True, blank=True, related_name='orders')
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text='Tax rate applied at time of order',
    )
    tax_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Tax amount calculated at time of order',
    )
    tax_type = models.CharField(
        max_length=10, blank=True,
        help_text='inclusive or exclusive at time of order',
    )

    order_number = models.CharField(
        max_length=20, blank=True, db_index=True,
        help_text='Daily sequential order number, e.g. ORD-20260319-001',
    )

    objects = BranchScopedManager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['branch', 'status', 'created_at']),
            models.Index(fields=['branch', 'created_at']),
            models.Index(fields=['waiter', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Order {self.order_number or self.id} - Space {self.table.number if self.table else 'N/A'}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_order_number():
        from django.utils import timezone as tz
        today = tz.localdate()
        date_str = today.strftime('%y%m%d')
        prefix = f'{date_str}-'
        last = Order.objects.filter(
            order_number__startswith=prefix,
        ).order_by('-order_number').values_list('order_number', flat=True).first()
        if last:
            seq = int(last.rsplit('-', 1)[-1]) + 1
        else:
            seq = 1
        return f'{prefix}{seq:03d}'

    def delete(self, *args, **kwargs):
        table = self.table
        super().delete(*args, **kwargs)
        if table and not table.orders.filter(status='active').exists():
            table.status = 'available'
            table.save()

    def get_subtotal(self):
        """Sum of item prices (before any tax adjustment)."""
        # Use prefetched cache if available, otherwise aggregate in DB
        if 'items' in self.__dict__.get('_prefetched_objects_cache', {}):
            return sum(item.get_subtotal() for item in self.items.all())
        from django.db.models import Sum, F
        return self.items.aggregate(
            total=Sum(F('unit_price') * F('quantity'))
        )['total'] or 0

    def get_total(self):
        """Final amount including tax. For exclusive tax, adds tax on top."""
        subtotal = self.get_subtotal()
        if self.tax_type == 'exclusive' and self.tax_amount:
            return subtotal + self.tax_amount
        return subtotal

    def get_item_count(self):
        if 'items' in self.__dict__.get('_prefetched_objects_cache', {}):
            return sum(item.quantity for item in self.items.all())
        from django.db.models import Sum
        return self.items.aggregate(total=Sum('quantity'))['total'] or 0


class OrderItem(models.Model):
    PREP_STATUS_CHOICES = [
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    notes = models.CharField(max_length=250, blank=True, help_text="Special requests")
    preparation_status = models.CharField(
        max_length=10, choices=PREP_STATUS_CHOICES, default='preparing',
    )
    ready_acknowledged = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['order', 'preparation_status']),
        ]

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.title}"

    def get_subtotal(self):
        return self.unit_price * self.quantity


class StationRequest(models.Model):
    """Request from kitchen/bar staff to the waiter — e.g. edit or cancel an item."""
    TYPE_CHOICES = [
        ('edit', 'Edit Request'),
        ('cancel', 'Cancel Request'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ]

    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='station_requests')
    request_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    message = models.TextField(help_text="Reason or details for the request")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    requested_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='station_requests_made',
    )
    responded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='station_requests_responded',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_request_type_display()} — {self.order_item}"


class BranchMenuAvailability(models.Model):
    """Per-branch availability override for menu items.

    If no row exists for a branch+item, the item's global ``is_available``
    flag is used.  When a row exists, ``is_available`` on this model takes
    precedence for that branch.
    """

    branch = models.ForeignKey(
        'branches.Branch', on_delete=models.CASCADE,
        related_name='menu_availability',
    )
    menu_item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE,
        related_name='branch_availability',
    )
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = ('branch', 'menu_item')
        verbose_name_plural = 'branch menu availability'

    def __str__(self):
        status = 'available' if self.is_available else 'unavailable'
        return f"{self.menu_item.title} @ {self.branch.name}: {status}"
