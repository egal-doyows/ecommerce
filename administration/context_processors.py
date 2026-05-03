def admin_role(request):
    """Add role flags to template context for all groups."""
    if not request.user.is_authenticated:
        return {}

    user = request.user
    is_manager = False

    if user.is_superuser:
        # Treat superuser as effectively Owner+Manager for template gating.
        is_manager = True
        ctx = {
            'is_owner':     True,
            'is_manager':   True,
            'is_supervisor': False,
            'is_server':    False,
            'is_cashier':   False,
            'is_kitchen':   False,
            'is_attendant': False,
            'is_promoter':  False,
        }
    else:
        groups = set(user.groups.values_list('name', flat=True))
        is_manager = 'Manager' in groups
        ctx = {
            'is_owner':     'Owner'      in groups,
            'is_manager':   is_manager,
            'is_supervisor': 'Supervisor' in groups,
            'is_server':    'Server'     in groups,
            'is_cashier':   'Cashier'    in groups,
            'is_kitchen':   'Kitchen'    in groups,
            'is_attendant': 'Attendant'  in groups,
            'is_promoter':  'Promoter'   in groups,
        }

    # Pending PO count for managers (and owners)
    if ctx.get('is_manager') or ctx.get('is_owner') or user.is_superuser:
        from purchasing.models import PurchaseOrder
        ctx['pending_po_count'] = PurchaseOrder.objects.filter(status='pending').count()

    return ctx
