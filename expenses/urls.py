from django.urls import path
from . import views

urlpatterns = [
    path('', views.expense_list, name='expense-list'),
    path('add/', views.expense_create, name='expense-create'),
    path('<int:pk>/', views.expense_detail, name='expense-detail'),
    path('<int:pk>/edit/', views.expense_edit, name='expense-edit'),
    path('<int:pk>/delete/', views.expense_delete, name='expense-delete'),
    path('<int:pk>/approve/', views.expense_approve, name='expense-approve'),
    path('<int:pk>/reject/', views.expense_reject, name='expense-reject'),
    path('<int:pk>/cancel/', views.expense_cancel, name='expense-cancel'),
    path('<int:pk>/pdf/', views.expense_pdf, name='expense-pdf'),
    path('summary/', views.expense_summary, name='expense-summary'),
    path('categories/', views.category_list, name='expense-category-list'),
    path('categories/add/', views.category_create, name='expense-category-create'),
    path('categories/<int:pk>/edit/', views.category_edit, name='expense-category-edit'),
]
