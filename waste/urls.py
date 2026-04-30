from django.urls import path
from . import views

urlpatterns = [
    path('', views.waste_list, name='waste-list'),
    path('log/', views.waste_create, name='waste-create'),
    path('<int:pk>/', views.waste_detail, name='waste-detail'),
    path('<int:pk>/pdf/', views.waste_pdf, name='waste-pdf'),
    path('summary/', views.waste_summary, name='waste-summary'),
]
