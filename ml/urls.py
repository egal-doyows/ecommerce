from django.urls import path

from . import views

urlpatterns = [
    path('', views.ml_index, name='ml-index'),
    path('insights/', views.insights_for_supervisors, name='ml-supervisor-insights'),
    path('prep-list/', views.prep_list, name='ml-prep-list'),
    path('weekday-forecast/', views.forecast_by_weekday, name='ml-weekday-forecast'),
    path('reorders/', views.reorders, name='ml-reorders'),
    path('reorders/<int:pk>/dismiss/', views.dismiss_reorder, name='ml-reorder-dismiss'),
    path('exceptions/', views.exceptions, name='ml-exceptions'),
    path('exceptions/<int:pk>/dismiss/', views.dismiss_exception, name='ml-exception-dismiss'),
    path('upsell/', views.upsell, name='ml-upsell'),
    path('menu-engineering/', views.menu_engineering, name='ml-menu-engineering'),
]
