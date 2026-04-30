from django.urls import path
from . import views

urlpatterns = [
    path('select/', views.post_login_branch_select, name='branch-select-landing'),
    path('switch/', views.switch_branch, name='switch-branch'),
    path('', views.branch_list, name='branch-list'),
    path('add/', views.branch_create, name='branch-create'),
    path('<int:pk>/edit/', views.branch_edit, name='branch-edit'),
    path('<int:pk>/assign/', views.branch_assign_staff, name='branch-assign-staff'),
    path('<int:pk>/remove/<int:user_id>/', views.branch_remove_staff, name='branch-remove-staff'),
]
