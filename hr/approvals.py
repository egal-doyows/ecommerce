from administration.approvals import register
from django.urls import reverse


@register
def pending_transfers(request):
    user = request.user
    is_overall = user.is_superuser or user.groups.filter(
        name__in=['Owner', 'Overall Manager']
    ).exists()
    if not is_overall:
        return None
    from hr.models import TransferRequest
    qs = TransferRequest.objects.filter(status='pending').exclude(
        requested_by=request.user,
    )
    return {
        'label': 'Transfer Requests',
        'count': qs.count(),
        'url': reverse('hr-transfer-list'),
        'icon': 'fa-solid fa-people-arrows',
        'priority': 3,
    }


@register
def pending_leaves(request):
    from hr.models import LeaveRequest
    qs = LeaveRequest.objects.filter(status='pending').exclude(
        employee__user=request.user,
    )
    branch = getattr(request, 'branch', None)
    if branch:
        qs = qs.filter(employee__user__branch_assignments__branch=branch)
    return {
        'label': 'Leave Requests',
        'count': qs.count(),
        'url': reverse('hr-leave-list') + '?status=pending',
        'icon': 'fa-solid fa-calendar-xmark',
        'priority': 6,
    }
