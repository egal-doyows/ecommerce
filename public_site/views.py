from django.shortcuts import render

from menu.models import Category, MenuItem, RestaurantSettings


def _settings():
    return RestaurantSettings.objects.first()


def home(request):
    featured = list(
        MenuItem.objects
        .filter(is_featured=True, is_available=True)
        .select_related('category')[:3]
    )
    return render(request, 'public_site/landing.html', {
        'settings': _settings(),
        'featured': featured,
    })


def contact(request):
    return render(request, 'public_site/contact.html', {
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
        items = list(cat.items.filter(is_available=True).order_by('title'))
        if items:
            grouped.append((cat, items))
    return render(request, 'public_site/menu.html', {
        'settings': _settings(),
        'grouped': grouped,
        'has_any_items': MenuItem.objects.filter(is_available=True).exists(),
    })
