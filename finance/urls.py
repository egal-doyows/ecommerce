from django.urls import path
from . import views

urlpatterns = [
    path('', views.finance_dashboard, name='finance-dashboard'),
    path('profit-loss/', views.profit_loss, name='finance-profit-loss'),
    path('cash-flow/', views.cash_flow, name='finance-cash-flow'),
    path('expense-report/', views.expense_report, name='finance-expense-report'),
    path('wastage-report/', views.wastage_report, name='finance-wastage-report'),
    path('payroll-report/', views.payroll_report, name='finance-payroll-report'),
    path('receivables/', views.receivables_report, name='finance-receivables'),
    path('payables/', views.payables_report, name='finance-payables'),
]
