from django.urls import path
from . import views

urlpatterns = [
    path('', views.waste_list, name='wastage-list'),
    path('log/', views.waste_create, name='wastage-create'),
    path('<int:pk>/', views.waste_detail, name='wastage-detail'),
    path('<int:pk>/pdf/', views.waste_pdf, name='wastage-pdf'),
    path('summary/', views.waste_summary, name='wastage-summary'),
]
