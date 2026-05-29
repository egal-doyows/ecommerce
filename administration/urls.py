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

    # Accompaniments
    path('accompaniments/', views.accompaniment_list, name='admin-accompaniment-list'),
    path('accompaniments/create/', views.accompaniment_group_create, name='admin-accompaniment-create'),
    path('accompaniments/<int:pk>/edit/', views.accompaniment_group_edit, name='admin-accompaniment-edit'),
    path('accompaniments/<int:pk>/delete/', views.accompaniment_group_delete, name='admin-accompaniment-delete'),
    path('accompaniments/<int:group_id>/option/add/', views.accompaniment_option_add, name='admin-accompaniment-option-add'),
    path('accompaniments/option/<int:pk>/edit/', views.accompaniment_option_edit, name='admin-accompaniment-option-edit'),
    path('accompaniments/option/<int:pk>/delete/', views.accompaniment_option_delete, name='admin-accompaniment-option-delete'),

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
    path('voided-orders/', views.voided_order_list, name='admin-voided-orders'),

    # Shifts
    path('shifts/', views.shift_list_admin, name='admin-shift-list'),
    path('shifts/<int:shift_id>/reopen/', views.shift_reopen, name='admin-shift-reopen'),
    path('shifts/<int:shift_id>/close/', views.shift_reclose, name='admin-shift-reclose'),
    path('shifts/<int:shift_id>/edit/', views.shift_edit, name='admin-shift-edit'),

    # Settings
    path('settings/', views.settings_view, name='admin-settings'),

    # Job openings (careers content)
    path('jobs/', views.job_opening_list, name='admin-job-list'),
    path('jobs/create/', views.job_opening_create, name='admin-job-create'),
    path('jobs/<int:pk>/edit/', views.job_opening_edit, name='admin-job-edit'),
    path('jobs/<int:pk>/delete/', views.job_opening_delete, name='admin-job-delete'),

    # Reports
    path('reports/', views.reports_view, name='admin-reports'),

    # Accounts
    path('accounts/', views.accounts_overview, name='admin-accounts'),
    path('accounts/transfer/', views.transfer_funds, name='admin-transfer-funds'),
    path('accounts/<int:pk>/edit/', views.account_edit, name='admin-account-edit-form'),
    path('accounts/<int:pk>/', views.account_detail, name='admin-account-detail'),
]
