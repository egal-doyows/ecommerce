from decimal import Decimal, ROUND_HALF_UP

from django.db import models


class TaxConfiguration(models.Model):
    TAX_TYPE_CHOICES = [
        ('exclusive', 'Exclusive (added on top of price)'),
        ('inclusive', 'Inclusive (price already includes tax)'),
    ]

    is_enabled = models.BooleanField(
        default=False,
        help_text='Enable or disable tax calculation on orders',
    )
    tax_name = models.CharField(
        max_length=50, default='VAT',
        help_text='Display name for the tax (e.g. VAT, GST, Sales Tax)',
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('16.00'),
        help_text='Tax rate as a percentage (e.g. 16.00 for 16%)',
    )
    tax_type = models.CharField(
        max_length=10, choices=TAX_TYPE_CHOICES, default='inclusive',
        help_text='Exclusive: tax added on top. Inclusive: price already includes tax.',
    )
    tax_number = models.CharField(
        max_length=50, blank=True,
        help_text='Tax registration / PIN number (shown on receipts)',
    )

    class Meta:
        verbose_name = 'Tax Configuration'
        verbose_name_plural = 'Tax Configuration'

    def __str__(self):
        status = 'Enabled' if self.is_enabled else 'Disabled'
        return f"{self.tax_name} {self.tax_rate}% ({self.get_tax_type_display()}) — {status}"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def calculate(self, subtotal):
        """
        Given a subtotal (sum of item prices), return (display_subtotal, tax_amount, total).

        Exclusive: subtotal is pre-tax, tax is added on top.
        Inclusive: subtotal already includes tax, we extract tax from within.
        """
        if not self.is_enabled or self.tax_rate <= 0:
            return subtotal, Decimal('0'), subtotal

        rate = self.tax_rate / Decimal('100')

        if self.tax_type == 'exclusive':
            tax_amount = (subtotal * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total = subtotal + tax_amount
            return subtotal, tax_amount, total
        else:
            # Inclusive: price already contains tax
            tax_amount = (subtotal * rate / (1 + rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            pre_tax = subtotal - tax_amount
            return pre_tax, tax_amount, subtotal
