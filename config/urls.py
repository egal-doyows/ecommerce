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
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

from menu.views import service_worker_view
from core.views import health_check

urlpatterns = [
    path('health/', health_check, name='health-check'),
    path('healthz/', health_check, name='healthz'),
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
    path('wastage/', include('wastage.urls')),
    path('expenses/', include('expenses.urls')),
    path('hr/', include('hr.urls')),
    path('finance/', include('finance.urls')),
    path('tax/', include('tax.urls')),
    path('stocks/', include('stocks.urls')),
    path('assets/', include('assets.urls')),
    path('branches/', include('branches.urls')),
    path('api/v1/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)