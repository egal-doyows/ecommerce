from django.db import models


class CustomerFeedback(models.Model):
    RATING_CHOICES = [(value, str(value)) for value in range(1, 6)]
    VISIT_CHOICES = [
        ('dine_in', 'Dine in'),
        ('takeaway', 'Takeaway'),
        ('delivery', 'Delivery'),
    ]
    STATUS_CHOICES = [
        ('new', 'New'),
        ('reviewed', 'Reviewed'),
        ('resolved', 'Resolved'),
    ]

    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    visit_type = models.CharField(max_length=20, choices=VISIT_CHOICES, blank=True)
    liked_most = models.CharField(max_length=240, blank=True)
    improvement = models.TextField(blank=True)
    name = models.CharField(max_length=100, blank=True)
    contact = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='new', db_index=True)
    staff_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'customer feedback'

    def __str__(self):
        return f'{self.rating}/5 feedback — {self.created_at:%d %b %Y}'

