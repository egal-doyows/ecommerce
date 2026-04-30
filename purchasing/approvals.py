from administration.approvals import register
from django.urls import reverse


@register
def pending_purchase_orders(request):
    from purchasing.models import PurchaseOrder
    qs = PurchaseOrder.objects.filter(status='pending').exclude(
        created_by=request.user,
    )
    branch = getattr(request, 'branch', None)
    user = request.user
    is_overall = user.is_superuser or user.groups.filter(
        name__in=['Owner', 'Overall Manager']
    ).exists()
    if branch and not is_overall:
        qs = qs.filter(branch=branch)
    return {
        'label': 'Purchase Orders',
        'count': qs.count(),
        'url': reverse('po-list') + '?status=pending',
        'icon': 'fa-solid fa-clipboard-list',
        'priority': 5,
    }
