from django.urls import path
from . import views


urlpatterns = [
    path('', views.pos_home, name='pos'),
    path('item/<slug:slug>/', views.item_detail, name='item-detail'),
    path('category/<slug:category_slug>/', views.category_filter, name='category-filter'),
    path('place-order/', views.place_order, name='place-order'),
    path('orders/', views.order_list, name='order-list'),
    path('orders/<int:order_id>/', views.order_detail, name='order-detail'),
    path('orders/<int:order_id>/status/', views.order_update_status, name='order-update-status'),
    path('tables/', views.tables_view, name='tables'),
    path('shift/', views.shift_view, name='shift'),
    path('shift/clock-in/', views.shift_clock_in, name='shift-clock-in'),
    path('shift/clock-out/', views.shift_clock_out, name='shift-clock-out'),
    path('shift/<int:shift_id>/', views.shift_detail, name='shift-detail'),
]
