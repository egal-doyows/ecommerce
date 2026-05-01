from django.shortcuts import render

from menu.models import Category, MenuItem, RestaurantSettings


def _settings():
    return RestaurantSettings.objects.first()


def home(request):
    return render(request, 'public_site/landing.html', {
        'settings': _settings(),
    })


def menu(request):
    categories = (
        Category.objects
        .prefetch_related('items')
        .order_by('name')
    )
    grouped = []
    for cat in categories:
        items = list(cat.items.filter(is_available=True).order_by('name'))
        if items:
            grouped.append((cat, items))
    return render(request, 'public_site/menu.html', {
        'settings': _settings(),
        'grouped': grouped,
        'has_any_items': MenuItem.objects.filter(is_available=True).exists(),
    })
