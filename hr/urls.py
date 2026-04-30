from django.urls import path

from . import views

urlpatterns = [
    # Dashboard
    path('', views.hr_dashboard, name='hr-dashboard'),

    # Employees
    path('employees/', views.employee_list, name='hr-employee-list'),
    path('employees/add/', views.employee_create, name='hr-employee-create'),
    path('employees/<int:pk>/', views.employee_detail, name='hr-employee-detail'),
    path('employees/<int:pk>/edit/', views.employee_edit, name='hr-employee-edit'),
    path('employees/<int:pk>/status/', views.employee_status, name='hr-employee-status'),
    path('employees/<int:pk>/transfer/', views.employee_transfer, name='hr-employee-transfer'),

    # Emergency contacts
    path('employees/<int:emp_pk>/contacts/add/', views.emergency_contact_add, name='hr-contact-add'),
    path('contacts/<int:pk>/edit/', views.emergency_contact_edit, name='hr-contact-edit'),
    path('contacts/<int:pk>/delete/', views.emergency_contact_delete, name='hr-contact-delete'),

    # Documents
    path('employees/<int:emp_pk>/documents/add/', views.document_upload, name='hr-document-upload'),
    path('documents/<int:pk>/delete/', views.document_delete, name='hr-document-delete'),

    # Departments
    path('departments/', views.department_list, name='hr-department-list'),
    path('departments/add/', views.department_create, name='hr-department-create'),
    path('departments/<int:pk>/edit/', views.department_edit, name='hr-department-edit'),

    # Positions
    path('positions/', views.position_list, name='hr-position-list'),
    path('positions/add/', views.position_create, name='hr-position-create'),
    path('positions/<int:pk>/edit/', views.position_edit, name='hr-position-edit'),

    # Leave management
    path('leave/', views.leave_list, name='hr-leave-list'),
    path('leave/request/', views.leave_request, name='hr-leave-request'),
    path('leave/<int:pk>/', views.leave_detail, name='hr-leave-detail'),
    path('leave/<int:pk>/approve/', views.leave_approve, name='hr-leave-approve'),
    path('leave/<int:pk>/reject/', views.leave_reject, name='hr-leave-reject'),
    path('leave/<int:pk>/cancel/', views.leave_cancel, name='hr-leave-cancel'),
    path('leave/<int:pk>/pdf/', views.leave_pdf, name='hr-leave-pdf'),

    # Leave types (manager)
    path('leave-types/', views.leave_type_list, name='hr-leave-type-list'),
    path('leave-types/add/', views.leave_type_create, name='hr-leave-type-create'),
    path('leave-types/<int:pk>/edit/', views.leave_type_edit, name='hr-leave-type-edit'),

    # Transfer requests (approval workflow)
    path('transfers/', views.transfer_request_list, name='hr-transfer-list'),
    path('transfers/<int:pk>/approve/', views.transfer_request_approve, name='hr-transfer-approve'),
    path('transfers/<int:pk>/reject/', views.transfer_request_reject, name='hr-transfer-reject'),
]
