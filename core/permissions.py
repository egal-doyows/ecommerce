"""Centralised role checks and permission decorators.

Every app was duplicating _is_admin_user / _is_manager / manager_required etc.
This module is the single source of truth for role-based access control.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect

# ═══════════════════════════════════════════════════════════════════════
#  Role group names — single place to update if groups are renamed
# ═══════════════════════════════════════════════════════════════════════

OWNER = 'Owner'
OVERALL_MANAGER = 'Overall Manager'
BRANCH_MANAGER = 'Branch Manager'
SUPERVISOR = 'Supervisor'
MARKETING = 'Marketing'
FRONT_SERVICE = 'Front Service'
ATTENDANT = 'Attendant'
DISPLAY = 'Display'

MANAGER_GROUPS = [OWNER, BRANCH_MANAGER, OVERALL_MANAGER]
ADMIN_GROUPS = [OWNER, BRANCH_MANAGER, OVERALL_MANAGER, SUPERVISOR]

# ═══════════════════════════════════════════════════════════════════════
#  Role check functions
# ═══════════════════════════════════════════════════════════════════════


def is_admin_user(user):
    """Superuser, Owner, Branch Manager, Overall Manager, or Supervisor."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=ADMIN_GROUPS).exists()


def is_manager(user):
    """Superuser, Owner, Branch Manager, or Overall Manager (not Supervisor)."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=MANAGER_GROUPS).exists()


def is_overall_manager(user):
    """Superuser, Owner, or Overall Manager."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=[OWNER, OVERALL_MANAGER]).exists()


def has_full_access(user):
    """Superuser or Owner."""
    return user.is_superuser or user.groups.filter(name=OWNER).exists()


# ═══════════════════════════════════════════════════════════════════════
#  Decorators
# ═══════════════════════════════════════════════════════════════════════


def _role_decorator(check_fn, *, deny_message, deny_redirect):
    """Factory that builds a view decorator from a role-check function."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url='my-login')
        def wrapper(request, *args, **kwargs):
            if not check_fn(request.user):
                messages.error(request, deny_message)
                return redirect(deny_redirect)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_required(view_func):
    """Managers + Supervisors."""
    return _role_decorator(
        is_admin_user,
        deny_message='You do not have permission to access this page.',
        deny_redirect='dashboard',
    )(view_func)


def manager_required(view_func):
    """Managers only (not Supervisors)."""
    return _role_decorator(
        is_manager,
        deny_message='You do not have permission to perform this action.',
        deny_redirect='admin-dashboard',
    )(view_func)


def overall_manager_required(view_func):
    """Overall Managers + Owner + superuser only."""
    return _role_decorator(
        is_overall_manager,
        deny_message='Only overall managers can access this page.',
        deny_redirect='admin-dashboard',
    )(view_func)


def full_access_required(view_func):
    """Superuser or Owner only."""
    return _role_decorator(
        has_full_access,
        deny_message='Only the administrator can perform this action.',
        deny_redirect='admin-dashboard',
    )(view_func)
