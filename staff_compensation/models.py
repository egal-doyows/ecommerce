import calendar
from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class StaffCompensation(models.Model):
    """
    Defines how a staff member is compensated.
    Set once during user creation; can be updated by admin.
    """

    COMPENSATION_TYPE_CHOICES = [
        ('commission', 'Commission'),
        ('salary', 'Salary'),
    ]

    PAYMENT_FREQUENCY_CHOICES = [
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-Weekly'),
        ('monthly', 'Monthly'),
    ]

    COMMISSION_SCOPE_CHOICES = [
        ('regular', 'Regular Items Only'),
        ('premium', 'Premium Items Only'),
        ('both', 'Both Regular & Premium'),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='compensation',
    )
    compensation_type = models.CharField(
        max_length=10, choices=COMPENSATION_TYPE_CHOICES,
    )
    is_commission_only = models.BooleanField(
        default=False,
        help_text=(
            "Non-login staff who exist only for commission attribution. "
            "Replaces the legacy 'Attendant' group: login is blocked, but "
            "the user is still included in commission queries."
        ),
    )

    # Commission fields
    commission_scope = models.CharField(
        max_length=10, choices=COMMISSION_SCOPE_CHOICES, default='both',
        help_text="Which item tiers this staff member earns commission on",
    )
    commission_rate_regular = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Commission % on regular items (e.g. 10 = 10%)",
    )
    commission_rate_premium = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Commission % on premium items (e.g. 15 = 15%)",
    )

    # Salary fields
    salary_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Fixed salary amount per payment period",
    )
    payment_frequency = models.CharField(
        max_length=10, choices=PAYMENT_FREQUENCY_CHOICES,
        default='monthly',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Staff Compensation'
        verbose_name_plural = 'Staff Compensation'

    def __str__(self):
        if self.compensation_type == 'commission':
            scope = self.get_commission_scope_display()
            return f"{self.user.username} — commission ({scope})"
        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        return f"{self.user.username} — {symbol} {self.salary_amount} {self.get_payment_frequency_display()}"

    def _get_orders(self, start_date=None, end_date=None):
        """Return paid orders this user earns commission on.
        - Attendant / Server: orders where they are the waiter.
        - Promoter: orders where they are the created_by.
        """
        from django.db.models import Q
        from menu.models import Order
        orders = Order.objects.filter(
            Q(waiter=self.user) | Q(created_by=self.user),
            status='paid',
        ).distinct()
        if start_date:
            orders = orders.filter(created_at__gte=start_date)
        if end_date:
            orders = orders.filter(created_at__lte=end_date)
        return orders

    def get_current_month_range(self):
        """Return (start, end) datetimes for the current calendar month."""
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return month_start, now

    def get_month_range(self, year, month):
        """Return (start, end) datetimes for a specific calendar month."""
        tz = timezone.get_current_timezone()
        month_start = timezone.datetime(year, month, 1, tzinfo=tz)
        last_day = calendar.monthrange(year, month)[1]
        month_end = timezone.datetime(year, month, last_day, 23, 59, 59, tzinfo=tz)
        return month_start, month_end

    def _get_eligible_tiers(self):
        """Return the list of item_tier values this staff earns commission on."""
        if self.commission_scope == 'regular':
            return ['regular']
        elif self.commission_scope == 'premium':
            return ['premium']
        return ['regular', 'premium']

    def get_total_commission(self, start_date=None, end_date=None):
        """
        Calculate total commission earned from paid orders in a date range.
        Only includes order items matching the staff's commission scope,
        applying the correct rate per tier.
        """
        if self.compensation_type != 'commission':
            return Decimal('0')

        from menu.models import OrderItem
        eligible_tiers = self._get_eligible_tiers()
        orders = self._get_orders(start_date, end_date)

        total_commission = Decimal('0')
        for order in orders:
            for item in order.items.select_related('menu_item').all():
                tier = item.menu_item.item_tier
                if tier not in eligible_tiers:
                    continue
                subtotal = item.get_subtotal()
                if tier == 'premium' and self.commission_rate_premium:
                    total_commission += subtotal * self.commission_rate_premium / Decimal('100')
                elif tier == 'regular' and self.commission_rate_regular:
                    total_commission += subtotal * self.commission_rate_regular / Decimal('100')

        return total_commission.quantize(Decimal('0.01'))

    def get_eligible_sales(self, start_date=None, end_date=None):
        """Total sales from items matching this staff's commission scope."""
        eligible_tiers = self._get_eligible_tiers()
        orders = self._get_orders(start_date, end_date)
        total = Decimal('0')
        for order in orders:
            for item in order.items.select_related('menu_item').all():
                if item.menu_item.item_tier in eligible_tiers:
                    total += item.get_subtotal()
        return total

    def get_current_month_commission(self):
        start, end = self.get_current_month_range()
        return self.get_total_commission(start, end)

    def get_current_month_sales(self):
        start, end = self.get_current_month_range()
        return sum(order.get_total() for order in self._get_orders(start, end))

    def get_current_month_eligible_sales(self):
        start, end = self.get_current_month_range()
        return self.get_eligible_sales(start, end)

    def get_current_month_order_count(self):
        start, end = self.get_current_month_range()
        return self._get_orders(start, end).count()

    def get_total_sales(self, start_date=None, end_date=None):
        return sum(order.get_total() for order in self._get_orders(start_date, end_date))

    def get_order_count(self, start_date=None, end_date=None):
        return self._get_orders(start_date, end_date).count()

    def get_daily_breakdown(self, start_date=None, end_date=None):
        """
        Return a list of dicts with daily commission data, sorted by date descending.
        Each dict: {date, order_count, total_sales, eligible_sales, commission}
        """
        if self.compensation_type != 'commission':
            return []

        eligible_tiers = self._get_eligible_tiers()
        orders = self._get_orders(start_date, end_date).prefetch_related('items__menu_item')

        daily = {}
        for order in orders:
            day = order.created_at.date()
            if day not in daily:
                daily[day] = {
                    'date': day,
                    'order_count': 0,
                    'total_sales': Decimal('0'),
                    'eligible_sales': Decimal('0'),
                    'commission': Decimal('0'),
                }
            daily[day]['order_count'] += 1
            daily[day]['total_sales'] += order.get_total()
            for item in order.items.all():
                tier = item.menu_item.item_tier
                subtotal = item.get_subtotal()
                if tier in eligible_tiers:
                    daily[day]['eligible_sales'] += subtotal
                    if tier == 'premium' and self.commission_rate_premium:
                        daily[day]['commission'] += subtotal * self.commission_rate_premium / Decimal('100')
                    elif tier == 'regular' and self.commission_rate_regular:
                        daily[day]['commission'] += subtotal * self.commission_rate_regular / Decimal('100')

        # Round commissions
        for d in daily.values():
            d['commission'] = d['commission'].quantize(Decimal('0.01'))

        return sorted(daily.values(), key=lambda x: x['date'], reverse=True)


class StaffBankDetails(models.Model):
    """Optional bank details for a staff member, used when paying via bank."""
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='bank_details',
    )
    bank_name = models.CharField(max_length=100)
    account_name = models.CharField(max_length=150)
    account_number = models.CharField(max_length=30)
    branch = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = 'Staff Bank Details'
        verbose_name_plural = 'Staff Bank Details'

    def __str__(self):
        return f"{self.user.username} — {self.bank_name} ({self.account_number})"


class PaymentRecord(models.Model):
    """
    Tracks monthly commission/salary payments.
    One record per staff per month. Created automatically for past months;
    admin marks as PAID once the staff member has been paid.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
    ]

    DISBURSEMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('bank', 'Bank Transfer'),
        ('mpesa', 'M-Pesa'),
    ]

    staff = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='payment_records',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_sales = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Total sales for the period",
    )
    eligible_sales = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Sales from items matching commission scope",
    )
    order_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of paid orders in the period",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    month_label = models.CharField(
        max_length=20, blank=True,
        help_text="e.g. 'February 2026'",
    )
    payment_type = models.CharField(
        max_length=10,
        choices=StaffCompensation.COMPENSATION_TYPE_CHOICES,
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='pending',
    )
    disbursement_method = models.CharField(
        max_length=10, choices=DISBURSEMENT_METHOD_CHOICES,
        blank=True, help_text="How the payment was disbursed",
    )
    mpesa_transaction_code = models.CharField(
        max_length=20, blank=True,
        help_text="M-Pesa transaction code when paid via M-Pesa",
    )
    amount_paid = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Running total of how much has been paid so far",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-period_end']
        verbose_name = 'Payment Record'
        verbose_name_plural = 'Payment Records'

    @property
    def remaining(self):
        return self.amount - self.amount_paid

    def __str__(self):
        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        return f"{self.staff.username} — {symbol} {self.amount} ({self.month_label})"

    def mark_paid(self, method='cash', pay_amount=None, mpesa_code=''):
        if pay_amount is None:
            pay_amount = self.remaining
        self.amount_paid += pay_amount
        self.disbursement_method = method
        if method == 'mpesa':
            self.mpesa_transaction_code = mpesa_code
        if self.amount_paid >= self.amount:
            self.status = 'paid'
            self.paid_at = timezone.now()
        self.save()


def generate_past_month_records(user):
    """
    Create PaymentRecords for any past months that don't have one yet.
    Called when viewing a staff member's compensation detail.
    """
    try:
        comp = user.compensation
    except StaffCompensation.DoesNotExist:
        return

    if comp.compensation_type != 'commission':
        return

    now = timezone.now()
    # Start from the month the user was created
    start_month = user.date_joined.replace(day=1)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    cursor = start_month
    while cursor < current_month_start:
        year, month = cursor.year, cursor.month
        period_start = cursor.date().replace(day=1)
        last_day = calendar.monthrange(year, month)[1]
        period_end = cursor.date().replace(day=last_day)

        # Skip if record already exists
        if not PaymentRecord.objects.filter(
            staff=user, period_start=period_start, period_end=period_end,
        ).exists():
            month_start_dt, month_end_dt = comp.get_month_range(year, month)
            commission = comp.get_total_commission(month_start_dt, month_end_dt)
            total_sales = comp.get_total_sales(month_start_dt, month_end_dt)
            eligible_sales = comp.get_eligible_sales(month_start_dt, month_end_dt)
            order_count = comp.get_order_count(month_start_dt, month_end_dt)

            if commission > 0 or total_sales > 0:
                PaymentRecord.objects.create(
                    staff=user,
                    amount=commission,
                    total_sales=total_sales,
                    eligible_sales=eligible_sales,
                    order_count=order_count,
                    period_start=period_start,
                    period_end=period_end,
                    month_label=cursor.strftime('%B %Y'),
                    payment_type='commission',
                    status='pending',
                )

        # Move to next month
        if month == 12:
            cursor = cursor.replace(year=year + 1, month=1)
        else:
            cursor = cursor.replace(month=month + 1)


def generate_current_month_record(user):
    """
    Create or update a pending PaymentRecord for the current month.
    Calculates outstanding = total commission earned - already paid this month.
    """
    try:
        comp = user.compensation
    except StaffCompensation.DoesNotExist:
        return None

    if comp.compensation_type != 'commission':
        return None

    now = timezone.now()
    year, month = now.year, now.month
    period_start = now.date().replace(day=1)
    last_day = calendar.monthrange(year, month)[1]
    period_end = now.date().replace(day=last_day)

    month_start_dt, month_end_dt = comp.get_month_range(year, month)
    commission = comp.get_total_commission(month_start_dt, month_end_dt)
    total_sales = comp.get_total_sales(month_start_dt, month_end_dt)
    eligible_sales = comp.get_eligible_sales(month_start_dt, month_end_dt)
    order_count = comp.get_order_count(month_start_dt, month_end_dt)

    if commission <= 0 and total_sales <= 0:
        return None

    # How much has already been paid this month?
    from django.db.models import Sum
    already_paid = PaymentRecord.objects.filter(
        staff=user,
        period_start=period_start,
        period_end=period_end,
        status='paid',
    ).aggregate(total=Sum('amount'))['total'] or 0

    outstanding = commission - already_paid

    # Find existing pending record for this month
    pending_record = PaymentRecord.objects.filter(
        staff=user,
        period_start=period_start,
        period_end=period_end,
        status='pending',
    ).first()

    if outstanding <= 0:
        # Fully paid — remove any stale pending record
        if pending_record:
            pending_record.delete()
        return None

    if pending_record:
        # Update the pending record with current outstanding
        pending_record.amount = outstanding
        pending_record.total_sales = total_sales
        pending_record.eligible_sales = eligible_sales
        pending_record.order_count = order_count
        pending_record.save()
        return pending_record
    else:
        # Create a new pending record for the outstanding amount
        return PaymentRecord.objects.create(
            staff=user,
            amount=outstanding,
            total_sales=total_sales,
            eligible_sales=eligible_sales,
            order_count=order_count,
            period_start=period_start,
            period_end=period_end,
            month_label=now.strftime('%B %Y'),
            payment_type='commission',
            status='pending',
        )
