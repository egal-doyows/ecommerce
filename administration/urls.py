from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path('', views.admin_dashboard, name='admin-dashboard'),

    # Staff
    path('staff/', views.staff_list, name='admin-staff-list'),
    path('staff/create/', views.staff_create, name='admin-staff-create'),
    path('staff/<int:user_id>/edit/', views.staff_edit, name='admin-staff-edit'),
    path('staff/<int:user_id>/delete/', views.staff_delete, name='admin-staff-delete'),

    # Categories
    path('categories/', views.category_list, name='admin-category-list'),
    path('categories/create/', views.category_create, name='admin-category-create'),
    path('categories/<int:pk>/edit/', views.category_edit, name='admin-category-edit'),
    path('categories/<int:pk>/delete/', views.category_delete, name='admin-category-delete'),

    # Menu Items
    path('menu/', views.menu_item_list, name='admin-menu-list'),
    path('menu/create/', views.menu_item_create, name='admin-menu-create'),
    path('menu/<int:pk>/edit/', views.menu_item_edit, name='admin-menu-edit'),
    path('menu/<int:pk>/delete/', views.menu_item_delete, name='admin-menu-delete'),

    # Recipes
    path('menu/<int:menu_item_id>/recipe/add/', views.recipe_add, name='admin-recipe-add'),
    path('recipe/<int:pk>/delete/', views.recipe_delete, name='admin-recipe-delete'),

    # Inventory
    path('inventory/', views.inventory_list, name='admin-inventory-list'),
    path('inventory/create/', views.inventory_create, name='admin-inventory-create'),
    path('inventory/<int:pk>/edit/', views.inventory_edit, name='admin-inventory-edit'),
    path('inventory/<int:pk>/delete/', views.inventory_delete, name='admin-inventory-delete'),

    # Tables
    path('tables/', views.table_list, name='admin-table-list'),
    path('tables/create/', views.table_create, name='admin-table-create'),
    path('tables/<int:pk>/edit/', views.table_edit, name='admin-table-edit'),
    path('tables/<int:pk>/delete/', views.table_delete, name='admin-table-delete'),

    # Orders
    path('orders/', views.order_list_admin, name='admin-order-list'),

    # Shifts
    path('shifts/', views.shift_list_admin, name='admin-shift-list'),

    # Settings
    path('settings/', views.settings_view, name='admin-settings'),

    # Reports
    path('reports/', views.reports_view, name='admin-reports'),

    # Accounts
    path('accounts/', views.accounts_overview, name='admin-accounts'),
    path('accounts/transfer/', views.transfer_funds, name='admin-transfer-funds'),
    path('accounts/<int:pk>/', views.account_detail, name='admin-account-detail'),

    # Branch Menu Availability
    path('menu/availability/', views.branch_menu_availability, name='admin-branch-menu'),
    path('menu/<int:menu_item_id>/toggle-availability/', views.toggle_branch_availability, name='admin-toggle-availability'),

    # Overall Manager — Cross-branch analytics
    path('overall/', views.overall_dashboard, name='overall-dashboard'),
]
