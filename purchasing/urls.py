from django.urls import path
from . import views

urlpatterns = [
    path('', views.po_list, name='po-list'),
    path('create/', views.po_create, name='po-create'),
    path('low-stock/', views.po_from_low_stock, name='po-from-low-stock'),
    path('<int:pk>/', views.po_detail, name='po-detail'),
    path('<int:pk>/edit/', views.po_edit, name='po-edit'),
    path('<int:pk>/items/add/', views.po_add_item, name='po-add-item'),
    path('<int:pk>/items/<int:item_pk>/update/', views.po_update_item, name='po-update-item'),
    path('<int:pk>/items/<int:item_pk>/remove/', views.po_remove_item, name='po-remove-item'),
    path('<int:pk>/change-supplier/', views.po_change_supplier, name='po-change-supplier'),
    path('<int:pk>/pdf/', views.po_pdf, name='po-pdf'),
    path('<int:pk>/submit/', views.po_submit, name='po-submit'),
    path('<int:pk>/approve/', views.po_approve, name='po-approve'),
    path('<int:pk>/receive/', views.po_receive, name='po-receive'),
    path('<int:pk>/cancel/', views.po_cancel, name='po-cancel'),
]
