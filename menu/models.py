from django.db import models
from django.urls import reverse
from django.contrib.auth.models import User


class RestaurantSettings(models.Model):
    name = models.CharField(max_length=150, default='RestoPOS')
    tagline = models.CharField(max_length=250, blank=True, default='Your Favourite Restaurant')
    phone = models.CharField(max_length=30, blank=True)
    website = models.CharField(max_length=150, blank=True)
    logo = models.ImageField(upload_to='restaurant/', blank=True)

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


class MenuItem(models.Model):
    category = models.ForeignKey(Category, related_name='items', on_delete=models.CASCADE)
    title = models.CharField(max_length=250)
    slug = models.SlugField(max_length=250)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=2)
    image = models.ImageField(upload_to='images/', blank=True)
    is_available = models.BooleanField(default=True)
    preparation_time = models.PositiveIntegerField(default=10, help_text="Estimated prep time in minutes")

    class Meta:
        verbose_name_plural = 'menu items'
        ordering = ['category', 'title']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('item-detail', args=[self.slug])


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
        return f"Table {self.number}"


class Shift(models.Model):
    waiter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shifts')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    starting_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0)
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
    ]

    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, related_name='orders')
    waiter = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='orders')
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, blank=True)
    mpesa_code = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} - Table {self.table.number if self.table else 'N/A'}"

    def get_total(self):
        return sum(item.get_subtotal() for item in self.items.all())

    def get_item_count(self):
        return sum(item.quantity for item in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=250, blank=True, help_text="Special requests")

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.title}"

    def get_subtotal(self):
        return self.menu_item.price * self.quantity
