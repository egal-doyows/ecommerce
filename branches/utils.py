from .models import Branch


def resolve_branch(request):
    """Return the target branch for object creation.

    For Overall Managers (and superusers), check POST for an explicit
    ``target_branch`` selection.  Everyone else uses ``request.branch``.
    """
    if request.method == 'POST':
        is_overall = (
            request.user.is_superuser
            or request.user.groups.filter(name__in=['Owner', 'Overall Manager']).exists()
        )
        if is_overall:
            branch_id = request.POST.get('target_branch')
            if branch_id:
                try:
                    return Branch.objects.get(pk=branch_id, is_active=True)
                except Branch.DoesNotExist:
                    pass
    return request.branch
