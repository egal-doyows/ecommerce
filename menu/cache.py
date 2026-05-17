"""
Tiny cache wrappers for read-mostly singletons that show up on every
public request.

Both RestaurantSettings and the Category list change rarely (admin
edits, not customer activity) but are dereferenced multiple times per
page via template context processors. Caching cuts the DB chatter to
near-zero without compromising freshness — invalidation is wired up via
signals in `menu.signals`.
"""

from django.core.cache import cache


REST_SETTINGS_KEY = 'menu:rest_settings:v1'
CATEGORIES_KEY = 'menu:categories:v1'
TTL_SECONDS = 60


def get_restaurant_settings():
    """Cached RestaurantSettings singleton. Falls back to a fresh load on miss."""
    value = cache.get(REST_SETTINGS_KEY)
    if value is None:
        from .models import RestaurantSettings  # avoid AppRegistry import cycle
        value = RestaurantSettings.load()
        cache.set(REST_SETTINGS_KEY, value, TTL_SECONDS)
    return value


def get_categories():
    """Cached Category list. Returns a list (not a QuerySet) — already materialised."""
    value = cache.get(CATEGORIES_KEY)
    if value is None:
        from .models import Category  # avoid AppRegistry import cycle
        value = list(Category.objects.order_by('name'))
        cache.set(CATEGORIES_KEY, value, TTL_SECONDS)
    return value


def invalidate_restaurant_settings():
    cache.delete(REST_SETTINGS_KEY)


def invalidate_categories():
    cache.delete(CATEGORIES_KEY)
