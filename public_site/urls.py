from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='public-home'),
    path('menu/', views.menu, name='public-menu'),
    path('contact/', views.contact, name='public-contact'),
    path('site.webmanifest', views.webmanifest, name='public-webmanifest'),
    path('robots.txt', views.robots_txt, name='public-robots-txt'),
]
