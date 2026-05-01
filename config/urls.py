"""
URL configuration for ecommerce project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import path, include
from django.views.decorators.cache import never_cache

from django.conf import settings
from django.conf.urls.static import static

from menu.views import service_worker_view


@never_cache
def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok', 'database': 'ok'})
    except Exception:
        return JsonResponse({'status': 'error', 'database': 'error'}, status=503)


urlpatterns = [
    # Public marketing site lives at the root.
    path('', include('public_site.urls')),

    # Health check stays at the root so nginx / uptime monitors don't need
    # to know about the app prefix.
    path('health/', health_check, name='health-check'),
    path('healthz/', health_check, name='healthz'),

    # The full POS / back-office app lives under /restpos/.
    path('restpos/', include([
        path('sw.js', service_worker_view, name='service-worker'),
        path('admin/', admin.site.urls),
        path('', include('menu.urls')),
        path('cart/', include('cart.urls')),
        path('account/', include('account.urls')),
        path('compensation/', include('staff_compensation.urls')),
        path('manage/', include('administration.urls')),
        path('suppliers/', include('supplier.urls')),
        path('debtors/', include('debtor.urls')),
        path('purchasing/', include('purchasing.urls')),
        path('receiving/', include('receiving.urls')),
        path('waste/', include('waste.urls')),
        path('expenses/', include('expenses.urls')),
        path('hr/', include('hr.urls')),
        path('reports/', include('reports.urls')),
    ])),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)