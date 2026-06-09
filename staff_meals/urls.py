from django.urls import path
from . import views

urlpatterns = [
    path('', views.staff_meal_list, name='staff-meal-list'),
    path('log/', views.staff_meal_create, name='staff-meal-create'),
    path('summary/', views.staff_meal_summary, name='staff-meal-summary'),
    path('<int:pk>/', views.staff_meal_detail, name='staff-meal-detail'),
]
