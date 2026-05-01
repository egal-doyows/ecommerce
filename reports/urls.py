from django.urls import path

from . import views

urlpatterns = [
    path('', views.reports_index, name='reports-index'),
    path('profit-loss/', views.profit_loss, name='reports-profit-loss'),
    path('stock-on-hand/', views.stock_on_hand, name='reports-stock-on-hand'),
    path('aged-receivables/', views.aged_receivables, name='reports-aged-receivables'),
    path('audit-trail/', views.audit_trail, name='reports-audit-trail'),
    path('z-report/', views.z_report_list, name='reports-z-report'),
    path('z-report/<int:shift_id>/', views.z_report_detail, name='reports-z-report-detail'),
    path('daily-sales/', views.daily_sales, name='reports-daily-sales'),
    path('voids-log/', views.voids_log, name='reports-voids-log'),
]
