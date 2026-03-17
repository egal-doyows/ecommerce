from django.urls import path
from django.views.generic import RedirectView
from . import views


urlpatterns = [
    path('', RedirectView.as_view(pattern_name='waiter-login'), name='account-root'),

    # Login / Logout
    path('my-login', views.my_login, name='my-login'),
    path('waiter-login', views.waiter_login, name='waiter-login'),
    path('setup-login-code', views.setup_login_code, name='setup-login-code'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('user-logout', views.user_logout, name='user-logout'),

    # Account management
    path('profile-management', views.profile_management, name='profile-management'),
    path('delete-account', views.delete_account, name='delete-account'),
]
