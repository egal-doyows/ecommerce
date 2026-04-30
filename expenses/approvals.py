from administration.approvals import register
from django.urls import reverse


@register
def pending_expenses(request):
    from expenses.models import Expense
    qs = Expense.objects.filter(status='pending')
    branch = getattr(request, 'branch', None)
    if branch:
        qs = qs.filter(branch=branch)
    return {
        'label': 'Expenses',
        'count': qs.count(),
        'url': reverse('expense-list') + '?status=pending',
        'icon': 'fa-solid fa-file-invoice-dollar',
        'priority': 7,
    }
