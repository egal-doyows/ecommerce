from django.urls import path
from . import views

urlpatterns = [
    path('', views.compensation_overview, name='compensation-overview'),
    path('staff/<int:user_id>/', views.staff_detail, name='compensation-detail'),
    path('staff/<int:user_id>/edit/', views.edit_compensation, name='compensation-edit'),
    path('staff/<int:user_id>/bank/', views.bank_details_edit, name='bank-details-edit'),
    path('payments/', views.payment_list, name='payment-list'),
    path('payments/<int:pk>/pay/', views.pay_staff, name='pay-staff'),
    path('my-earnings/', views.my_earnings, name='my-earnings'),
]
