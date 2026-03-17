from django.urls import path
from . import views

urlpatterns = [
    path('', views.debtor_list, name='debtor-list'),
    path('create/', views.debtor_create, name='debtor-create'),
    path('<int:pk>/', views.debtor_detail, name='debtor-detail'),
    path('<int:pk>/edit/', views.debtor_edit, name='debtor-edit'),
    path('<int:pk>/transaction/', views.transaction_create, name='debtor-transaction-create'),
    path('<int:pk>/receive-payment/', views.receive_payment, name='debtor-receive-payment'),
]
