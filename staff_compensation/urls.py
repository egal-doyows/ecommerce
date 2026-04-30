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
    # Advance requests
    path('advances/', views.advance_request_list, name='advance-list'),
    path('advances/request/', views.advance_request_create, name='advance-request'),
    path('advances/request-for-staff/', views.advance_request_for_staff, name='advance-request-for-staff'),
    path('advances/<int:pk>/review/', views.advance_request_review, name='advance-review'),
    path('advances/<int:pk>/cancel/', views.advance_cancel, name='advance-cancel'),
    path('advances/<int:pk>/pdf/', views.advance_pdf, name='advance-pdf'),
    path('advances/verify/', views.advance_verify, name='advance-verify'),
    # Payroll
    path('payroll/', views.payroll_list, name='payroll-list'),
    path('payroll/<int:pk>/', views.payroll_detail, name='payroll-detail'),
    path('payroll/generate/', views.payroll_generate, name='payroll-generate'),
    path('payroll/<int:pk>/delete/', views.payroll_delete, name='payroll-delete'),
    path('payroll/<int:pk>/pdf/', views.payroll_pdf, name='payroll-pdf'),
    path('payslip/<int:pk>/pdf/', views.payslip_pdf, name='payslip-pdf'),
]
