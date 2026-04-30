def admin_role(request):
    """Add role flags to template context for all groups.

    Optimised to avoid DB queries for unauthenticated users and
    non-manager roles that never see pending counts.
    """
    if not request.user.is_authenticated:
        return {}

    user = request.user

    if user.is_superuser:
        ctx = {
            'is_manager': True,
            'is_overall_manager': True,
            'is_admin_role': True,
            'is_supervisor': False,
            'is_front_service': False,
            'is_cashier': False,
        }
    else:
        groups = set(user.groups.values_list('name', flat=True))
        is_admin_role = 'Owner' in groups
        is_manager = is_admin_role or 'Branch Manager' in groups or 'Overall Manager' in groups
        is_overall_manager = is_admin_role or 'Overall Manager' in groups
        ctx = {
            'is_manager': is_manager,
            'is_overall_manager': is_overall_manager,
            'is_admin_role': is_admin_role,
            'is_supervisor': 'Supervisor' in groups,
            'is_front_service': 'Front Service' in groups,
            'is_cashier': 'Cashier' in groups,
            'is_attendant': 'Attendant' in groups,
        }

    # Branch context (cheap — already resolved by middleware)
    branch = getattr(request, 'branch', None)
    ctx['current_branch'] = branch
    ctx['user_branches'] = getattr(request, 'user_branches', [])

    # Only run pending-count queries for users who actually see them
    # (managers viewing admin templates). Skip for waiters/cashiers/etc.
    is_manager = ctx.get('is_manager', False)

    if is_manager or user.is_superuser:
        from administration.approvals import get_pending_approvals
        pending_approvals = get_pending_approvals(request)
        ctx['pending_approvals'] = pending_approvals
        ctx['pending_approval_total'] = sum(a['count'] for a in pending_approvals)

    return ctx
