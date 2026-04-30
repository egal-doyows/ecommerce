"""
Service layer for order-related business logic.

Keeps views thin by extracting stock deduction, payment recording,
order state transitions, and tax calculations into testable functions.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404

from .models import (
    MenuItem, Table, Order, OrderItem, Shift,
    _InsufficientStock,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  ORDER STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════

VALID_TRANSITIONS = {
    'active': {'paid', 'cancelled'},
    'paid': set(),          # terminal state
    'cancelled': set(),     # terminal state
}


class InvalidTransition(Exception):
    """Raised when an invalid order status transition is attempted."""
    pass


def validate_transition(current_status, new_status):
    """Check if a status transition is allowed. Raises InvalidTransition if not."""
    allowed = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise InvalidTransition(
            f'Cannot transition from "{current_status}" to "{new_status}". '
            f'Allowed transitions: {allowed or "none (terminal state)"}.'
        )


# ═══════════════════════════════════════════════════════════════════════
#  ORDER PLACEMENT
# ═══════════════════════════════════════════════════════════════════════

@transaction.atomic
def place_order(*, cart_items, table, waiter, created_by=None,
                shift=None, notes='', branch=None):
    """
    Create an order with stock deduction — fully atomic.

    Args:
        cart_items: iterable of dicts with 'product' (MenuItem), 'qty', 'price'
        table: Table instance
        waiter: User who serves the order
        created_by: User who created the order (e.g. Marketing staff)
        shift: active Shift instance
        notes: order notes
        branch: Branch instance

    Returns:
        The created Order instance.

    Raises:
        _InsufficientStock if any ingredient is out of stock
        (entire transaction rolls back).
    """
    from tax.models import TaxConfiguration

    tax_cfg = TaxConfiguration.load()
    cart_subtotal = sum(
        Decimal(str(item['price'])) * item['qty'] for item in cart_items
    )
    _, tax_amount, _ = tax_cfg.calculate(cart_subtotal)

    order = Order.objects.create(
        table=table,
        waiter=waiter,
        created_by=created_by,
        shift=shift,
        notes=notes,
        status='active',
        branch=branch,
        tax_rate=tax_cfg.tax_rate if tax_cfg.is_enabled else 0,
        tax_amount=tax_amount,
        tax_type=tax_cfg.tax_type if tax_cfg.is_enabled else '',
    )

    from stocks.tracking import log_movement

    for item in cart_items:
        product = item['product']
        qty = item['qty']
        OrderItem.objects.create(
            order=order,
            menu_item=product,
            quantity=qty,
            unit_price=item['price'],
        )
        # Stock deduction — if this raises, the entire transaction rolls back
        product.deduct_stock(qty)

        # Log stock movements
        if product.is_direct_sale:
            log_movement(
                product.inventory_item, 'sale', -qty,
                reference=order.order_number, user=waiter, branch=branch,
            )
        else:
            for recipe in product.recipe_items.select_related('inventory_item').all():
                log_movement(
                    recipe.inventory_item, 'sale',
                    -(recipe.quantity_required * qty),
                    reference=order.order_number, user=waiter, branch=branch,
                )

    table.status = 'occupied'
    table.save()

    return order


# ═══════════════════════════════════════════════════════════════════════
#  ORDER STATUS UPDATE
# ═══════════════════════════════════════════════════════════════════════

@transaction.atomic
def update_order_status(order, new_status, *, payment_method='',
                        mpesa_code='', debtor=None, user=None):
    """
    Transition an order to a new status with all side effects.

    - Validates the transition is legal
    - Handles payment recording
    - Restores stock on cancellation
    - Frees the table when order is completed/cancelled

    Args:
        order: Order instance
        new_status: target status string
        payment_method: for 'paid' transitions
        mpesa_code: last 4 chars of M-Pesa code
        debtor: Debtor instance for credit sales
        user: the user performing the action

    Raises:
        InvalidTransition if the status change is not allowed
    """
    validate_transition(order.status, new_status)

    if new_status == 'paid':
        order.payment_method = payment_method
        if payment_method == 'mpesa':
            order.mpesa_code = mpesa_code
        if payment_method == 'credit' and debtor:
            order.debtor = debtor

    # Restore stock on cancellation
    if new_status == 'cancelled' and order.status == 'active':
        from stocks.tracking import log_movement
        for oi in order.items.select_related('menu_item').all():
            try:
                oi.menu_item.restore_stock(oi.quantity)
                # Log restoration movements
                mi = oi.menu_item
                if mi.is_direct_sale:
                    log_movement(
                        mi.inventory_item, 'cancel', oi.quantity,
                        reference=order.order_number, user=user, branch=order.branch,
                    )
                else:
                    for recipe in mi.recipe_items.select_related('inventory_item').all():
                        log_movement(
                            recipe.inventory_item, 'cancel',
                            recipe.quantity_required * oi.quantity,
                            reference=order.order_number, user=user, branch=order.branch,
                        )
            except Exception:
                logger.warning("Stock restore failed for %s", oi.menu_item.title)

    order.status = new_status
    order.save()

    # Record payment in accounts
    if new_status == 'paid':
        if order.payment_method == 'credit' and order.debtor:
            from debtor.models import DebtorTransaction
            DebtorTransaction.objects.create(
                debtor=order.debtor,
                transaction_type='debit',
                amount=order.get_total(),
                description=f'Order #{order.id} — Space {order.table.number if order.table else "N/A"}',
                reference=str(order.id),
                created_by=user,
            )
        else:
            from administration.models import record_order_payment
            record_order_payment(order, created_by=user)

    # Free the table
    if new_status in ('paid', 'cancelled') and order.table:
        order.table.status = 'available'
        order.table.save()

    return order
