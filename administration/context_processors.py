def admin_role(request):
    """Add role flags to template context for all groups."""
    if not request.user.is_authenticated:
        return {}

    user = request.user
    is_manager = False

    if user.is_superuser:
        is_manager = True
        ctx = {
            'is_manager': True,
            'is_supervisor': False,
            'is_front_service': False,
            'is_cashier': False,
        }
    else:
        groups = set(user.groups.values_list('name', flat=True))
        is_manager = 'Manager' in groups
        ctx = {
            'is_manager': is_manager,
            'is_supervisor': 'Supervisor' in groups,
            'is_front_service': 'Front Service' in groups,
            'is_cashier': 'Cashier' in groups,
            'is_attendant': 'Attendant' in groups,
        }

    # Pending PO count for managers
    if is_manager or user.is_superuser:
        from purchasing.models import PurchaseOrder
        ctx['pending_po_count'] = PurchaseOrder.objects.filter(status='pending').count()

    return ctx
