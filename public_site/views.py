from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import cache_control

from menu.cache import get_restaurant_settings
from menu.models import Category, MenuItem


# Public pages are read-only and content changes rarely. A 5-minute
# public cache lets a CDN / reverse-proxy absorb the load and keeps the
# DB quiet under traffic. Pages with admin-changeable content still feel
# live because the cache is short and the context-processor cache is even
# shorter (60s).
PUBLIC_CACHE_SECONDS = 300


def _settings():
    return get_restaurant_settings()


@cache_control(public=True, max_age=PUBLIC_CACHE_SECONDS)
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


@cache_control(public=True, max_age=PUBLIC_CACHE_SECONDS)
def contact(request):
    return render(request, 'public_site/contact.html', {
        'settings': _settings(),
    })


@cache_control(max_age=86400)
def webmanifest(request):
    """PWA manifest. Lets browsers treat the site as an installable app."""
    s = _settings()
    name = (s.name if s else None) or 'Bean & Bite'
    return JsonResponse({
        'name': name,
        'short_name': name,
        'description': (s.tagline if s and s.tagline else 'Fresh-brewed coffee, hearty meals, and warm hospitality.'),
        'start_url': '/',
        'display': 'standalone',
        'background_color': '#FBF4E8',
        'theme_color': '#B83E1E',
        'icons': [
            {'src': static('public_site/img/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': static('public_site/img/icon-512.png'), 'sizes': '512x512', 'type': 'image/png'},
            {'src': static('public_site/img/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    })


@cache_control(max_age=86400)
def robots_txt(request):
    """Tell crawlers where the sitemap lives and which paths to skip."""
    host = f'{request.scheme}://{request.get_host()}'
    body = (
        'User-agent: *\n'
        'Disallow: /restpos/\n'
        'Disallow: /admin/\n'
        '\n'
        f'Sitemap: {host}/sitemap.xml\n'
    )
    return HttpResponse(body, content_type='text/plain; charset=utf-8')


@cache_control(public=True, max_age=PUBLIC_CACHE_SECONDS)
def menu(request):
    # Prefetch only the items that should appear on the public menu, and
    # pre-order them. The template then iterates the prefetched manager
    # without re-filtering — that's what defeats the prefetch.
    available_items = MenuItem.objects.filter(is_available=True).order_by('title')
    categories = (
        Category.objects
        .prefetch_related(Prefetch('items', queryset=available_items, to_attr='available_items'))
        .order_by('name')
    )
    grouped = [(c, c.available_items) for c in categories if c.available_items]
    return render(request, 'public_site/menu.html', {
        'settings': _settings(),
        'grouped': grouped,
        'has_any_items': any(items for _, items in grouped),
    })
