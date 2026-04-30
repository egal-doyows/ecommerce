from administration.approvals import register
from django.urls import reverse


@register
def pending_advances(request):
    user = request.user
    is_approver = user.is_superuser or user.groups.filter(
        name__in=['Owner', 'Overall Manager']
    ).exists()
    if not is_approver:
        return None
    from staff_compensation.models import AdvanceRequest
    from django.db.models import Q
    qs = AdvanceRequest.objects.filter(status='pending').exclude(
        Q(requested_by=user) | Q(employee=user),
    )
    branch = getattr(request, 'branch', None)
    if branch:
        qs = qs.filter(branch=branch)
    return {
        'label': 'Advance Requests',
        'count': qs.count(),
        'url': reverse('advance-list') + '?status=pending',
        'icon': 'fa-solid fa-money-bill-transfer',
        'priority': 4,
    }
