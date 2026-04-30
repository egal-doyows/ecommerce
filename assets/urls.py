from django.urls import path
from . import views

urlpatterns = [
    path('', views.asset_list, name='asset-list'),
    path('create/', views.asset_create, name='asset-create'),
    path('<int:pk>/edit/', views.asset_edit, name='asset-edit'),
    path('<int:pk>/delete/', views.asset_delete, name='asset-delete'),
    path('categories/', views.category_list, name='asset-category-list'),
    path('categories/create/', views.category_create, name='asset-category-create'),
    path('categories/<int:pk>/edit/', views.category_edit, name='asset-category-edit'),
    path('register-pdf/', views.asset_register_pdf, name='asset-register-pdf'),
]
