"""
Utility to log stock movements from anywhere in the codebase.

Usage:
    from stocks.tracking import log_movement
    log_movement(inventory_item, 'sale', -quantity, reference='ORD-240320-001', user=request.user, branch=branch)
"""
from .models import StockMovement


def log_movement(inventory_item, movement_type, quantity, reference='', notes='', user=None, branch=None):
    """Create a StockMovement record. Call AFTER the stock quantity has been updated."""
    inventory_item.refresh_from_db(fields=['stock_quantity'])
    StockMovement.objects.create(
        inventory_item=inventory_item,
        branch=branch or inventory_item.branch,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=inventory_item.stock_quantity,
        reference=reference,
        notes=notes,
        created_by=user,
    )
