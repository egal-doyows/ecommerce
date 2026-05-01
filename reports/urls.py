from django.urls import path

from . import views

urlpatterns = [
    path('', views.reports_index, name='reports-index'),
    path('profit-loss/', views.profit_loss, name='reports-profit-loss'),
    path('stock-on-hand/', views.stock_on_hand, name='reports-stock-on-hand'),
    path('aged-receivables/', views.aged_receivables, name='reports-aged-receivables'),
]
