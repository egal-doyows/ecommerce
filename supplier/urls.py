from django.urls import path
from . import views

urlpatterns = [
    path('', views.supplier_list, name='supplier-list'),
    path('create/', views.supplier_create, name='supplier-create'),
    path('<int:pk>/', views.supplier_detail, name='supplier-detail'),
    path('<int:pk>/edit/', views.supplier_edit, name='supplier-edit'),
    path('<int:pk>/transaction/', views.transaction_create, name='supplier-transaction-create'),
    path('<int:pk>/pay/', views.make_payment, name='supplier-make-payment'),
]
