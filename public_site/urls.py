from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='public-home'),
    path('menu/', views.menu, name='public-menu'),
]
