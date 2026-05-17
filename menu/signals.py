"""
Cache-invalidation signals for the read-mostly singletons.

Kept deliberately small — these signals fire on the admin-edit path,
not in any hot loop, so the perf cost is irrelevant.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache import invalidate_categories, invalidate_restaurant_settings
from .models import Category, RestaurantSettings


@receiver(post_save, sender=RestaurantSettings)
def _settings_changed(sender, **kwargs):
    invalidate_restaurant_settings()


@receiver([post_save, post_delete], sender=Category)
def _categories_changed(sender, **kwargs):
    invalidate_categories()
