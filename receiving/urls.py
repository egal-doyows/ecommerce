from django.urls import path
from . import views

urlpatterns = [
    path('', views.receipt_list, name='receipt-list'),
    path('receive/<int:po_pk>/', views.receipt_create, name='receipt-create'),
    path('<int:pk>/', views.receipt_detail, name='receipt-detail'),
    path('<int:pk>/pdf/', views.receipt_pdf, name='receipt-pdf'),
    path('po/<int:po_pk>/summary/', views.po_receiving_summary, name='po-receiving-summary'),
]
