from django.urls import path
from . import views

urlpatterns = [
    path('', views.movement_list, name='stock-movement-list'),
    path('adjustments/', views.adjustment_list, name='stock-adjustment-list'),
    path('adjustments/create/', views.adjustment_create, name='stock-adjustment-create'),
    path('adjustments/<int:pk>/', views.adjustment_detail, name='stock-adjustment-detail'),
    path('stocktake-pdf/', views.stocktake_pdf, name='stock-stocktake-pdf'),
]
