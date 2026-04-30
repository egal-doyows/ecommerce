from django.urls import path
from . import views

urlpatterns = [
    path('settings/', views.tax_settings, name='tax-settings'),
]
