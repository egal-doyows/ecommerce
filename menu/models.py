from decimal import Decimal

from django.db import models, transaction
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator


class RestaurantSettings(models.Model):
    name = models.CharField(max_length=150, default='Bean & Bite')
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

    # ── Delivery-platform commission rates (used by Channel Margin report) ──
    ubereats_commission_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('27'),
        help_text='Uber Eats commission percentage taken on gross revenue.',
    )
    glovo_commission_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('28'),
        help_text='Glovo commission percentage taken on gross revenue.',
    )
    bolt_commission_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('25'),
        help_text='Bolt Food commission percentage taken on gross revenue.',
    )
    jumia_commission_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('20'),
        help_text='Jumia Food commission percentage taken on gross revenue.',
    )

    # ── Public-site contact details (all optional) ──
    email = models.EmailField(
        blank=True,
        help_text='Public contact email shown on the Contact page and footer.',
    )
    address = models.TextField(
        blank=True,
        help_text='Street address. Use line breaks for multi-line.',
    )
    tax_number = models.CharField(
        max_length=50, blank=True,
        help_text='Tax / VAT / PIN number printed on customer receipts (e.g. KRA PIN).',
    )
    mpesa_till_number = models.CharField(
        max_length=20, blank=True,
        help_text='M-Pesa Till / Buy Goods number printed on customer receipts.',
    )
    map_embed_url = models.URLField(
        max_length=500, blank=True,
        help_text=(
            'Optional Google Maps embed URL. In Google Maps → Share → Embed a map → copy '
            'the src="..." value from the iframe. Shown on the Contact page if set.'
        ),
    )
    directions_url = models.URLField(
        max_length=500, blank=True,
        help_text='Optional "Get directions" link (full Google Maps URL).',
    )
    whatsapp_number = models.CharField(
        max_length=30, blank=True,
        help_text='Country code, no +, no spaces. e.g. 254712345678 → opens wa.me link.',
    )
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True, help_text='X / Twitter profile URL.')

    # ── Geolocation (used by ML weather-aware forecasting) ──
    latitude = models.DecimalField(
        max_digits=8, decimal_places=5, null=True, blank=True,
        help_text='Decimal degrees, e.g. -1.28333. Needed for weather-aware forecasting.',
    )
    longitude = models.DecimalField(
        max_digits=8, decimal_places=5, null=True, blank=True,
        help_text='Decimal degrees, e.g. 36.81667. Needed for weather-aware forecasting.',
    )

    ubereats_url = models.URLField(
        blank=True,
        help_text='Public restaurant page on Uber Eats. Leave blank to hide the button.',
    )
    glovo_url = models.URLField(
        blank=True,
        help_text='Public restaurant page on Glovo. Leave blank to hide the button.',
    )
    bolt_url = models.URLField(
        blank=True,
        help_text='Public restaurant page on Bolt Food. Leave blank to hide the button.',
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
    price = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    image = models.ImageField(upload_to='images/', blank=True)
    is_available = models.BooleanField(default=True)
    is_featured = models.BooleanField(
        default=False,
        help_text="Show this item in the 'Signature brews' section on the public landing page (up to 3 are shown).",
    )
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
    accompaniment_groups = models.ManyToManyField(
        'AccompanimentGroup', blank=True, related_name='menu_items',
        help_text="Choice groups offered with this item, e.g. 'Choose a side'.",
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
        Returns True on success, raises _InsufficientStock if a direct-sale item
        or any recipe ingredient is short.
        """
        if self.is_direct_sale:
            if not self.inventory_item.deduct(quantity):
                raise _InsufficientStock(self.inventory_item.name)
            return True
        return _deduct_recipe(self.recipe_items, quantity)

    @transaction.atomic
    def restore_stock(self, quantity=1):
        """Restore inventory when an order is cancelled."""
        if self.is_direct_sale:
            self.inventory_item.restore(quantity)
            return
        _restore_recipe(self.recipe_items, quantity)

    def current_unit_cost(self):
        """
        Cost-of-goods for one unit, computed from current inventory buying_prices.
        Returns Decimal('0') for untracked items (no inventory_item, no recipe).
        Snapshot this at order time — buying_price drifts on every receipt.
        """
        if self.is_direct_sale:
            return self.inventory_item.buying_price
        return _recipe_cost(self.recipe_items)

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


# ── Shared recipe stock/cost helpers ──
# Used by both MenuItem and AccompanimentOption, which each expose a
# `recipe_items` related manager of Recipe rows.

def _deduct_recipe(recipe_qs, quantity):
    """Deduct every ingredient in a recipe queryset. Returns True (incl. untracked)."""
    ingredients = list(recipe_qs.select_related('inventory_item'))
    if not ingredients:
        return True  # untracked
    for ingredient in ingredients:
        needed = ingredient.quantity_required * Decimal(str(quantity))
        if not ingredient.inventory_item.deduct(needed):
            raise _InsufficientStock(ingredient.inventory_item.name)
    return True


def _restore_recipe(recipe_qs, quantity):
    """Restore every ingredient in a recipe queryset."""
    for ingredient in recipe_qs.select_related('inventory_item'):
        needed = ingredient.quantity_required * Decimal(str(quantity))
        ingredient.inventory_item.restore(needed)


def _recipe_cost(recipe_qs):
    """Sum cost-of-goods across a recipe queryset at current buying_prices."""
    cost = Decimal('0')
    for r in recipe_qs.select_related('inventory_item'):
        cost += r.quantity_required * r.inventory_item.buying_price
    return cost


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


class AccompanimentGroup(models.Model):
    """
    A reusable choice group attached to menu items, e.g. "Choose a side".
    Single-choice: the customer picks exactly one option (when required).
    Attach to items via MenuItem.accompaniment_groups.
    """

    name = models.CharField(max_length=120, help_text='e.g. "Choose a side"')
    is_required = models.BooleanField(
        default=True,
        help_text='If ticked, one option must be chosen before the item is added.',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class AccompanimentOption(models.Model):
    """
    One choice within an AccompanimentGroup, e.g. "Fries" or "Plantain".
    `price_delta` is added to the item's base price (0 = free side).
    Stock/cost work exactly like MenuItem: link an `inventory_item` for a
    direct side, or add Recipe rows for a prepared one.
    """

    group = models.ForeignKey(
        AccompanimentGroup, on_delete=models.CASCADE, related_name='options',
    )
    label = models.CharField(max_length=120, help_text='e.g. "Plantain"')
    price_delta = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Added to the base item price. 0 for a free side.',
    )
    is_available = models.BooleanField(default=True)
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='accompaniment_options',
        help_text='For a direct-stock side. Leave blank and add a Recipe for a prepared side.',
    )
    inventory_quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=1,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Units of the linked inventory item consumed per pick (e.g. 0.02 kg of cheese). Matches the inventory column precision (2dp). Ignored when using a recipe.',
    )

    class Meta:
        ordering = ['group', 'label']

    def __str__(self):
        return f"{self.group.name}: {self.label}"

    @property
    def is_direct_sale(self):
        return self.inventory_item_id is not None

    @transaction.atomic
    def deduct_stock(self, quantity=1):
        if self.is_direct_sale:
            if not self.inventory_item.deduct(quantity * self.inventory_quantity):
                raise _InsufficientStock(self.inventory_item.name)
            return True
        return _deduct_recipe(self.recipe_items, quantity)

    @transaction.atomic
    def restore_stock(self, quantity=1):
        if self.is_direct_sale:
            self.inventory_item.restore(quantity * self.inventory_quantity)
            return
        _restore_recipe(self.recipe_items, quantity)

    def current_unit_cost(self):
        """Cost per single pick. For direct-sale options this is already
        multiplied by `inventory_quantity` so the caller can use it as the
        per-pick cost contribution without further scaling."""
        if self.is_direct_sale:
            return self.inventory_item.buying_price * self.inventory_quantity
        return _recipe_cost(self.recipe_items)


class Recipe(models.Model):
    """
    Links the InventoryItems consumed by a prepared item — either a MenuItem
    (e.g. Mango Juice = 1 Mango + 0.05 kg Sugar) or an AccompanimentOption
    (e.g. Fries = 0.2 kg Potato). Exactly one owner is set per row.
    """

    menu_item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name='recipe_items',
        null=True, blank=True,
    )
    accompaniment_option = models.ForeignKey(
        AccompanimentOption, on_delete=models.CASCADE, related_name='recipe_items',
        null=True, blank=True,
    )
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='used_in_recipes')
    quantity_required = models.DecimalField(
        max_digits=8, decimal_places=3,
        help_text="Amount of this ingredient consumed per 1 item sold",
    )

    class Meta:
        verbose_name_plural = 'recipes'
        constraints = [
            models.UniqueConstraint(
                fields=['menu_item', 'inventory_item'],
                condition=models.Q(menu_item__isnull=False),
                name='unique_menuitem_ingredient',
            ),
            models.UniqueConstraint(
                fields=['accompaniment_option', 'inventory_item'],
                condition=models.Q(accompaniment_option__isnull=False),
                name='unique_option_ingredient',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(menu_item__isnull=False, accompaniment_option__isnull=True)
                    | models.Q(menu_item__isnull=True, accompaniment_option__isnull=False)
                ),
                name='recipe_exactly_one_owner',
            ),
        ]

    def __str__(self):
        owner = self.menu_item.title if self.menu_item_id else str(self.accompaniment_option)
        return (
            f"{owner} — "
            f"{self.quantity_required} {self.inventory_item.get_unit_display()} "
            f"{self.inventory_item.name}"
        )


class Table(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('reserved', 'Reserved'),
    ]

    number = models.CharField(max_length=10, unique=True)
    capacity = models.PositiveIntegerField(default=4)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='available')

    class Meta:
        ordering = ['number']

    def __str__(self):
        return f"Table {self.number}"


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
    counted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='counted_shifts',
        help_text="Supervisor / manager who recorded the till count "
                  "(separation of duties — never the shift's own server)",
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    pending_close_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when the server requested clock-out and is waiting "
                  "for a supervisor to count the till. Cleared once the "
                  "supervisor records the count and finalizes ended_at.",
    )
    reopened_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when a manager reopens this closed shift for "
                  "correction. While set (and is_active=True) the shift is in "
                  "correction mode: the waiter's entries are dated to the "
                  "shift's own date. Cleared when the shift is re-closed.",
    )
    reopened_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reopened_shifts',
        help_text="Manager who reopened the shift for correction.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Shift #{self.id} — {self.waiter.username} ({self.started_at.strftime('%d %b %H:%M')})"

    @property
    def in_correction(self):
        """True while a manager has reopened this closed shift for correction.

        Data created or edited in this window is backdated to the shift's own
        date (see correction_timestamp) so reports land on the right business
        day. A normal open shift has reopened_at=None, so this stays False and
        no backdating ever leaks into regular operation.
        """
        return self.is_active and self.reopened_at is not None

    def correction_timestamp(self):
        """Timestamp to stamp on corrections made while in correction mode.

        Uses the shift's start time so corrected orders fall on the shift's
        original business day. The z-report groups by the shift FK (not by
        created_at), so a single fixed timestamp per shift is fine; what
        matters is that date-range reports and the cash drawer attribute the
        entry to the shift's day, not today.
        """
        return self.started_at

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
        # SQL aggregate avoids loading every paid order + its items into
        # Python just to sum them.
        from django.db.models import Sum, F, DecimalField
        return (
            OrderItem.objects.filter(order__shift=self, order__status='paid')
            .aggregate(t=Sum(F('unit_price') * F('quantity'), output_field=DecimalField()))
            ['t']
        ) or Decimal('0')

    def get_total_items(self):
        from django.db.models import Sum
        return (
            OrderItem.objects.filter(order__shift=self)
            .aggregate(t=Sum('quantity'))['t']
        ) or 0


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
        ('ubereats', 'Uber Eats'),
        ('glovo', 'Glovo'),
        ('bolt', 'Bolt Food'),
        ('jumia', 'Jumia Food'),
        ('split', 'Split (multiple modes)'),
    ]

    # Tender modes allowed on a split payment — money received at settlement.
    # Credit/marketplace are deferred settlements and excluded.
    SPLIT_METHODS = ('cash', 'mpesa', 'card')

    ORDER_TYPE_CHOICES = [
        ('dine_in', 'Dine-in'),
        ('takeaway', 'Takeaway'),
    ]

    SOURCE_CHOICES = [
        ('pos', 'POS / Walk-in'),
        ('phone', 'Phone'),
        ('ubereats', 'Uber Eats'),
        ('glovo', 'Glovo'),
        ('bolt', 'Bolt Food'),
        ('jumia', 'Jumia Food'),
        ('other', 'Other'),
    ]

    order_type = models.CharField(
        max_length=10, choices=ORDER_TYPE_CHOICES, default='dine_in',
    )
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default='pos',
        help_text="Where the order came from. Used to split walk-in vs marketplace revenue.",
    )
    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
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
    voided_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when the order was voided by a supervisor/manager",
    )
    offline_id = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text="Client-generated id for an order queued offline. Used to "
                  "deduplicate sync replays so a dropped response can't create "
                  "the same order twice. Empty for orders placed online.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One order per non-empty offline_id. Empty string (online orders)
            # is exempt via the condition, so it can repeat freely.
            models.UniqueConstraint(
                fields=['offline_id'],
                condition=~models.Q(offline_id=''),
                name='unique_offline_id',
            ),
        ]

    def __str__(self):
        if self.order_type == 'dine_in':
            return f"Order #{self.id} - Table {self.table.number if self.table else 'N/A'}"
        return f"Order #{self.id} - {self.get_order_type_display()}"

    def delete(self, *args, **kwargs):
        table = self.table
        super().delete(*args, **kwargs)
        if table and not table.orders.filter(status='active').exists():
            table.status = 'available'
            table.save()

    def get_subtotal(self):
        """Sum of line items before any order-level discount/comp."""
        return sum((item.get_subtotal() for item in self.items.all()), Decimal('0'))

    def get_total(self):
        """Amount actually charged: 0 for a comp, else subtotal minus the
        order-level discount (never below 0). This is the authoritative figure
        every payment / ledger / refund path uses, so discounts and comps can't
        silently over-credit the drawer."""
        if self.is_comp:
            return Decimal('0')
        total = self.get_subtotal() - (self.discount_amount or Decimal('0'))
        return total if total > 0 else Decimal('0')

    def get_item_count(self):
        return sum(item.quantity for item in self.items.all())

    def payment_breakdown(self):
        """Amount received per payment mode, as {method: Decimal}.

        The authoritative per-mode split used by the ledger and the reports.
        For a split order it reads the OrderPayment tender lines; for any
        single-mode order the whole get_total() is attributed to its one
        method. Comps (total 0) and unpaid orders return an empty mapping.
        """
        if self.payment_method == 'split':
            breakdown = {}
            for p in self.payments.all():
                breakdown[p.payment_method] = (
                    breakdown.get(p.payment_method, Decimal('0')) + p.amount
                )
            return breakdown
        total = self.get_total()
        if not self.payment_method or total <= 0:
            return {}
        return {self.payment_method: total}


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
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


class OrderItemOption(models.Model):
    """
    Snapshot of an accompaniment chosen for an OrderItem. The option's
    price_delta and cost are already folded into the parent OrderItem's
    all-in unit_price / unit_cost; these rows are the breakdown detail and
    survive even if the AccompanimentOption is later edited or deleted.
    """

    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='options')
    option = models.ForeignKey(
        AccompanimentOption, on_delete=models.SET_NULL, null=True, blank=True,
    )
    group_name = models.CharField(max_length=120, blank=True)
    label = models.CharField(max_length=120)
    price_delta = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    def __str__(self):
        return f"{self.order_item} — {self.label}"


class OrderPayment(models.Model):
    """One tender line of a split payment (e.g. 600 cash + 400 M-Pesa).

    Only created for orders whose payment_method is 'split'. The rows must sum
    to Order.get_total() — the order is always settled in full. Single-mode
    orders don't get rows; their breakdown is derived from payment_method.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    payment_method = models.CharField(max_length=10, choices=Order.PAYMENT_CHOICES)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    mpesa_code = models.CharField(
        max_length=4, blank=True,
        help_text="Last 4 characters of the M-Pesa code, for an M-Pesa line",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_payments',
    )

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f"Order #{self.order_id} — {self.get_payment_method_display()} {self.amount}"
