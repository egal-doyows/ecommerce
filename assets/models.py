from django.conf import settings
from django.db import models


class AssetCategory(models.Model):
    """Groups assets — e.g. Utensils, Machines, Vehicles, Furniture."""
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(
        max_length=50, blank=True,
        help_text='FontAwesome class, e.g. fa-blender',
    )

    class Meta:
        verbose_name_plural = 'asset categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Asset(models.Model):
    CONDITION_CHOICES = [
        ('new', 'New'),
        ('good', 'Good'),
        ('fair', 'Fair'),
        ('poor', 'Poor'),
        ('damaged', 'Damaged'),
        ('disposed', 'Disposed'),
    ]

    branch = models.ForeignKey(
        'branches.Branch', on_delete=models.CASCADE,
        null=True, blank=True, related_name='assets',
    )
    category = models.ForeignKey(
        AssetCategory, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assets',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='assets/', blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
    )
    condition = models.CharField(
        max_length=10, choices=CONDITION_CHOICES, default='good',
    )
    location = models.CharField(
        max_length=200, blank=True,
        help_text='Where this asset is kept, e.g. Kitchen, Store Room',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assets_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category__name', 'name']

    def __str__(self):
        return self.name

    @property
    def total_value(self):
        return self.purchase_price * self.quantity
