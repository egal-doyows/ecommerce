from django.urls import path

from . import views


urlpatterns = [
    path('', views.submit_feedback, name='feedback-submit'),
    path('thanks/', views.feedback_thanks, name='feedback-thanks'),
    path('manage/', views.feedback_dashboard, name='feedback-dashboard'),
    path('manage/<int:pk>/status/', views.update_feedback_status, name='feedback-status'),
]
