"""
Storage for ML outputs and run audit.

Trainers write predictions / detections here; reports read from these tables.
Every nightly trainer also writes a ModelRun row so ops can see model health
without log-diving.
"""

from django.db import models
from django.contrib.auth.models import User

from menu.models import MenuItem, InventoryItem, Shift


SOURCE_CHOICES = [
    ('ml', 'ML model'),
    ('baseline', 'Baseline (insufficient data)'),
]


class ModelRun(models.Model):
    """
    Audit row written by every Celery training task — success or failure.

    Powers the admin health view ("which models are green right now?") and
    the auto-failover gate ("ML beaten by baseline 7 runs in a row → fall back").
    """

    STATUS_CHOICES = [
        ('running', 'Running'),
        ('ok', 'OK'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped (no data)'),
    ]

    model_name = models.CharField(max_length=50, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='running')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_used = models.PositiveIntegerField(default=0)
    rows_written = models.PositiveIntegerField(default=0)
    # Backtest metric vs baseline. Lower is better for forecast (MAE),
    # higher is better for basket (lift). Trainer documents its own metric.
    metric_name = models.CharField(max_length=30, blank=True)
    metric_value = models.FloatField(null=True, blank=True)
    baseline_value = models.FloatField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['model_name', '-started_at']),
        ]

    def __str__(self):
        return f"{self.model_name} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"


class DemandForecast(models.Model):
    """
    One row = forecasted demand for one MenuItem on one date (optional hour).
    Nightly trainer rebuilds the forward 14-day window.
    """

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='forecasts')
    date = models.DateField(db_index=True)
    hour = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='0-23 for hourly forecast; null = whole-day forecast',
    )
    qty_p50 = models.FloatField(help_text='Median forecast (50th percentile)')
    qty_p90 = models.FloatField(help_text='Upper-bound forecast (90th percentile)')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='ml')
    trained_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'hour', 'menu_item']
        unique_together = ('menu_item', 'date', 'hour')
        indexes = [
            models.Index(fields=['date', 'menu_item']),
        ]

    def __str__(self):
        when = f"{self.date}" + (f" {self.hour:02d}:00" if self.hour is not None else "")
        return f"{self.menu_item.title} @ {when}: {self.qty_p50:.1f}"


class ReorderSuggestion(models.Model):
    """
    Suggested purchase quantity per inventory item, computed from
    DemandForecast × recipe consumption × current stock × lead time buffer.
    """

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('ordered', 'Ordered'),
        ('dismissed', 'Dismissed'),
    ]

    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name='reorder_suggestions',
    )
    suggested_qty = models.DecimalField(max_digits=10, decimal_places=2)
    needed_by = models.DateField(help_text='Order by this date to avoid stockout')
    days_of_cover = models.FloatField(
        help_text='Days of stock remaining at current run-rate when this was computed',
    )
    reason = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='ml')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    computed_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reorder_resolutions',
    )

    class Meta:
        ordering = ['needed_by', 'inventory_item']
        indexes = [
            models.Index(fields=['status', 'needed_by']),
        ]

    def __str__(self):
        return f"{self.inventory_item.name}: order {self.suggested_qty} by {self.needed_by}"


class AnomalyEvent(models.Model):
    """
    A flagged deviation from a person's (or item's) own baseline.

    Subject is either a staff User (cash variance, voids, comps) or an
    InventoryItem (stock variance). Always reviewable, always dismissible.
    """

    SUBJECT_TYPES = [
        ('user', 'Staff member'),
        ('inventory_item', 'Inventory item'),
    ]

    METRIC_CHOICES = [
        ('cash_variance', 'Cash variance per shift'),
        ('voids_per_shift', 'Voids per shift'),
        ('comps_per_shift', 'Comps per shift'),
        ('discount_pct', 'Discount % per shift'),
        ('stock_variance', 'Stock variance %'),
        ('supervisor_cash_variance', 'Cash variance (supervisor-attributed)'),
        ('count_latency_minutes', 'Till count latency (minutes)'),
        ('combined_loss_risk', 'Joint risk: voids + cash variance'),
    ]

    subject_type = models.CharField(max_length=20, choices=SUBJECT_TYPES, db_index=True)
    subject_id = models.PositiveIntegerField(db_index=True)
    subject_label = models.CharField(
        max_length=150,
        help_text='Cached display name so reports stay readable if subject is deleted',
    )
    metric = models.CharField(max_length=30, choices=METRIC_CHOICES)
    shift = models.ForeignKey(
        Shift, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='anomaly_events',
    )
    observed_value = models.FloatField()
    expected_value = models.FloatField(help_text='Person/item baseline (mean)')
    z_score = models.FloatField(help_text='Standard deviations from baseline')
    direction = models.CharField(
        max_length=4, choices=[('high', 'High'), ('low', 'Low')],
        help_text='Whether observation was above or below baseline',
    )
    detected_at = models.DateTimeField(auto_now_add=True)
    occurred_on = models.DateField(db_index=True)
    dismissed = models.BooleanField(default=False, db_index=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='dismissed_anomalies',
    )
    dismissal_reason = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='ml')

    class Meta:
        ordering = ['-occurred_on', '-z_score']
        indexes = [
            models.Index(fields=['dismissed', '-occurred_on']),
            models.Index(fields=['subject_type', 'subject_id']),
        ]

    def __str__(self):
        return f"{self.subject_label} {self.get_metric_display()} z={self.z_score:.1f}"


class BasketRule(models.Model):
    """
    Apriori association rule: when antecedent is in an order, consequent
    is in the same order with given confidence/lift.

    Refreshed weekly. The POS reads top rules for upsell suggestions.
    """

    antecedent = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name='basket_antecedents',
    )
    consequent = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name='basket_consequents',
    )
    support = models.FloatField(help_text='Fraction of orders containing both')
    confidence = models.FloatField(help_text='P(consequent | antecedent)')
    lift = models.FloatField(help_text='Confidence / P(consequent); > 1 = positive association')
    n_orders = models.PositiveIntegerField(help_text='Orders the rule was trained on')
    refreshed_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='ml')

    class Meta:
        ordering = ['-lift', '-confidence']
        unique_together = ('antecedent', 'consequent')
        indexes = [
            models.Index(fields=['antecedent', '-lift']),
        ]

    def __str__(self):
        return f"{self.antecedent.title} → {self.consequent.title} (lift {self.lift:.2f})"


class WeatherObservation(models.Model):
    """
    One row per calendar date — historical actuals and forward forecasts
    both land here, distinguished by `source`. Fetched nightly from Open-Meteo.

    The forecast trainer joins this on `date` and passes the numeric columns
    to Prophet as extra regressors.
    """

    SOURCE_KIND = [
        ('actual', 'Actual (historical)'),
        ('forecast', 'Forecast'),
    ]

    date = models.DateField(unique=True, db_index=True)
    source = models.CharField(max_length=10, choices=SOURCE_KIND)
    temp_max_c = models.FloatField(null=True, blank=True, help_text='Daily max temperature in °C')
    temp_min_c = models.FloatField(null=True, blank=True, help_text='Daily min temperature in °C')
    precipitation_mm = models.FloatField(null=True, blank=True, help_text='Total daily precipitation, mm')
    is_rainy = models.BooleanField(default=False, help_text='Precipitation > 0.5mm OR weather_code is rain/showers')
    weather_code = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text='WMO weather code (0 clear, 51-67 rain, 95-99 thunder, etc.)',
    )
    latitude = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    longitude = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [models.Index(fields=['source', '-date'])]

    def __str__(self):
        return f'{self.date} ({self.get_source_display()}): {self.temp_max_c}°C, {self.precipitation_mm}mm'


class MenuClass(models.Model):
    """
    Boston-matrix classification for each MenuItem over a window.

    Stars      = high margin, high popularity → feature them
    Plowhorses = low  margin, high popularity → reprice / re-cost
    Puzzles    = high margin, low  popularity → promote
    Dogs       = low  margin, low  popularity → drop
    """

    CLASS_CHOICES = [
        ('star', 'Star'),
        ('plowhorse', 'Plowhorse'),
        ('puzzle', 'Puzzle'),
        ('dog', 'Dog'),
    ]

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='menu_classes')
    classification = models.CharField(max_length=10, choices=CLASS_CHOICES, db_index=True)
    window_start = models.DateField()
    window_end = models.DateField()
    units_sold = models.PositiveIntegerField()
    revenue = models.DecimalField(max_digits=12, decimal_places=2)
    margin = models.DecimalField(max_digits=12, decimal_places=2)
    margin_pct = models.FloatField()
    popularity_pct = models.FloatField(help_text="This item's share of total units sold in window")
    computed_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='ml')

    class Meta:
        ordering = ['-window_end', 'menu_item']
        unique_together = ('menu_item', 'window_start', 'window_end')

    def __str__(self):
        return f"{self.menu_item.title}: {self.get_classification_display()} ({self.window_start} → {self.window_end})"
