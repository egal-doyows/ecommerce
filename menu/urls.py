from django.urls import path
from . import views
from . import api


urlpatterns = [
    path('', views.pos_home, name='pos'),
    path('item/<slug:slug>/', views.item_detail, name='item-detail'),
    path('category/<slug:category_slug>/', views.category_filter, name='category-filter'),
    path('place-order/', views.place_order, name='place-order'),
    path('orders/', views.order_list, name='order-list'),
    path('orders/<int:order_id>/', views.order_detail, name='order-detail'),
    path('orders/<int:order_id>/status/', views.order_update_status, name='order-update-status'),
    path('orders/<int:order_id>/edit-item/', views.order_edit_item, name='order-edit-item'),
    path('tables/', views.tables_view, name='tables'),
    path('tables/<int:table_id>/toggle-reserve/', views.table_toggle_reserve, name='table-toggle-reserve'),
    path('shift/', views.shift_view, name='shift'),
    path('shift/clock-in/', views.shift_clock_in, name='shift-clock-in'),
    path('shift/clock-out/', views.shift_clock_out, name='shift-clock-out'),
    path('shift/<int:shift_id>/', views.shift_detail, name='shift-detail'),
    path('offline/', views.offline_view, name='offline'),

    # JSON API for PWA offline support
    path('api/menu/', api.api_menu, name='api-menu'),
    path('api/tables/', api.api_tables, name='api-tables'),
    path('api/orders/', api.api_orders, name='api-orders'),
    path('api/place-order/', api.api_place_order, name='api-place-order'),
    path('api/orders/<int:order_id>/status/', api.api_update_order_status, name='api-update-order-status'),
    path('api/sync-status/', api.api_sync_status, name='api-sync-status'),
]
