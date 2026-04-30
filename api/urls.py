from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'categories', views.CategoryViewSet, basename='api-category')
router.register(r'menu-items', views.MenuItemViewSet, basename='api-menuitem')
router.register(r'tables', views.TableViewSet, basename='api-table')
router.register(r'orders', views.OrderViewSet, basename='api-order')
router.register(r'shifts', views.ShiftViewSet, basename='api-shift')

urlpatterns = [
    path('', include(router.urls)),
]
