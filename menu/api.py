"""JSON API endpoints for PWA offline support."""
import json
import logging

from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import get_object_or_404
from django.utils import timezone

from decimal import Decimal

from .models import (
    Category, MenuItem, Table, Order, OrderItem, Shift,
    AccompanimentOption, OrderItemOption, _InsufficientStock,
)
from .views import (
    _is_supervisor, _is_auto_shift_user, _ensure_shift, _restore_order_stock,
    _backdate_for_correction, _record_order_accounting,
    _reverse_order_payment_for_correction, _order_refund_blocked_reason,
)

logger = logging.getLogger(__name__)


@login_required(login_url='waiter-login')
@require_GET
def api_menu(request):
    """Return all available menu items with categories and accompaniments."""
    categories = list(Category.objects.values('id', 'name', 'slug', 'icon'))

    menu_qs = (
        MenuItem.objects.filter(is_available=True)
        .prefetch_related('accompaniment_groups__options')
    )
    items = []
    for mi in menu_qs:
        groups = []
        for g in mi.accompaniment_groups.all():
            opts = [
                {'id': o.id, 'label': o.label,
                 'delta': float(o.price_delta), 'group_name': g.name}
                for o in g.options.all() if o.is_available
            ]
            if opts:
                groups.append({
                    'id': g.id, 'name': g.name,
                    'required': g.is_required, 'options': opts,
                })
        items.append({
            'id': mi.id, 'title': mi.title, 'slug': mi.slug,
            'description': mi.description, 'price': float(mi.price),
            'category_id': mi.category_id,
            'image': f'/media/{mi.image}' if mi.image else '',
            'item_tier': mi.item_tier, 'preparation_time': mi.preparation_time,
            'accompaniment_groups': groups,
        })
    return JsonResponse({'categories': categories, 'items': items})


@login_required(login_url='waiter-login')
@require_GET
def api_tables(request):
    """Return all tables with status."""
    tables = list(Table.objects.values('id', 'number', 'capacity', 'status'))
    return JsonResponse({'tables': tables})


@login_required(login_url='waiter-login')
@require_GET
def api_orders(request):
    """Return orders for the current user."""
    base_qs = Order.objects.exclude(status='cancelled')
    if not (request.user.is_superuser or _is_supervisor(request.user)):
        from django.db.models import Q
        base_qs = base_qs.filter(Q(waiter=request.user) | Q(created_by=request.user))

    orders = []
    for order in base_qs.select_related('table', 'waiter').prefetch_related('items__menu_item'):
        items = []
        for oi in order.items.all():
            items.append({
                'id': oi.id,
                'menu_item_id': oi.menu_item_id,
                'menu_item_title': oi.menu_item.title,
                'quantity': oi.quantity,
                'unit_price': float(oi.unit_price),
                'subtotal': float(oi.get_subtotal()),
            })
        orders.append({
            'id': order.id,
            'table_id': order.table_id,
            'table_number': order.table.number if order.table else None,
            'waiter': order.waiter.username,
            'status': order.status,
            'payment_method': order.payment_method,
            'total': float(order.get_total()),
            'item_count': order.get_item_count(),
            'created_at': order.created_at.isoformat(),
            'items': items,
        })
    return JsonResponse({'orders': orders})


@login_required(login_url='waiter-login')
@require_POST
def api_place_order(request):
    """Place an order from offline sync queue."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    table_id = data.get('table_id')
    items = data.get('items', [])
    notes = data.get('notes', '')
    offline_id = data.get('offline_id', '')
    order_type = data.get('order_type', 'dine_in')
    source = data.get('source', 'pos')

    if order_type not in dict(Order.ORDER_TYPE_CHOICES):
        order_type = 'dine_in'
    if source not in dict(Order.SOURCE_CHOICES):
        source = 'pos'

    if not items:
        return JsonResponse({'error': 'Items required'}, status=400)
    if order_type == 'dine_in' and not table_id:
        return JsonResponse({'error': 'Table required for dine-in orders'}, status=400)

    table = get_object_or_404(Table, id=table_id) if table_id and order_type == 'dine_in' else None

    # Ensure shift exists
    if _is_auto_shift_user(request.user):
        _ensure_shift(request.user)

    active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
    if not active_shift:
        return JsonResponse({'error': 'No active shift'}, status=400)

    # Idempotency: if this offline_id was already synced (e.g. the response to a
    # prior attempt was lost and the client retried), return the existing order
    # instead of creating a duplicate.
    if offline_id:
        existing = Order.objects.filter(offline_id=offline_id).first()
        if existing is not None:
            return JsonResponse({
                'success': True,
                'order_id': existing.id,
                'offline_id': offline_id,
                'duplicate': True,
            })

    order_waiter = request.user
    order_created_by = None

    # Resolve cart lines: validate menu items, resolve accompaniments, compute
    # all-in unit_price / unit_cost. Validation happens before opening the atomic
    # block so a bad payload doesn't even start an order.
    from cart.views import resolve_options
    cart_items = []
    for cart_item in items:
        menu_item = get_object_or_404(MenuItem, id=cart_item['id'])
        qty = int(cart_item.get('qty', 1))
        raw_option_ids = cart_item.get('options') or cart_item.get('option_ids') or []
        option_ids = [int(o['id']) if isinstance(o, dict) else int(o) for o in raw_option_ids]
        resolved_opts, error = resolve_options(menu_item, option_ids)
        if error:
            return JsonResponse({'error': error}, status=400)
        cart_items.append({
            'product': menu_item,
            'qty': qty,
            'options': resolved_opts,
        })

    try:
        with transaction.atomic():
            order = Order.objects.create(
                table=table,
                order_type=order_type,
                source=source,
                waiter=order_waiter,
                created_by=order_created_by,
                shift=active_shift,
                notes=notes,
                status='active',
                offline_id=offline_id or '',
            )
            _backdate_for_correction(order)
            for cart_item in cart_items:
                product = cart_item['product']
                qty = cart_item['qty']
                opts = cart_item['options']

                option_objs = {}
                if opts:
                    option_objs = {
                        o.id: o for o in AccompanimentOption.objects
                            .select_related('inventory_item')
                            .prefetch_related('recipe_items__inventory_item')
                            .filter(id__in=[o['id'] for o in opts])
                    }
                option_delta = sum(
                    (Decimal(str(o['delta'])) for o in opts), Decimal('0'),
                )
                option_cost = sum(
                    (option_objs[o['id']].current_unit_cost()
                     for o in opts if o['id'] in option_objs),
                    Decimal('0'),
                )

                order_item = OrderItem.objects.create(
                    order=order,
                    menu_item=product,
                    quantity=qty,
                    unit_price=product.price + option_delta,
                    unit_cost=product.current_unit_cost() + option_cost,
                )
                for o in opts:
                    obj = option_objs.get(o['id'])
                    OrderItemOption.objects.create(
                        order_item=order_item,
                        option=obj,
                        group_name=o.get('group_name', ''),
                        label=o['label'],
                        price_delta=Decimal(str(o['delta'])),
                        unit_cost=obj.current_unit_cost() if obj else Decimal('0'),
                    )

                product.deduct_stock(qty)
                for o in opts:
                    obj = option_objs.get(o['id'])
                    if obj:
                        obj.deduct_stock(qty)

            if table:
                table.status = 'occupied'
                table.save()
    except _InsufficientStock as e:
        # Distinct 409 so the offline-sync client can surface "out of stock"
        # rather than retrying as a generic failure. _InsufficientStock is
        # raised with the inventory item name as its single argument.
        item_name = str(e)
        return JsonResponse({
            'error': f'Not enough stock for {item_name}',
            'insufficient_item': item_name,
        }, status=409)
    except (KeyError, ValueError, TypeError) as e:
        # Malformed payload — don't retry.
        logger.warning("Order placement payload invalid: %s", str(e))
        return JsonResponse({'error': 'Invalid order payload.'}, status=400)
    except IntegrityError as e:
        # Transient DB conflict (race, FK gone) — retry is reasonable.
        logger.warning("Order placement DB conflict: %s", str(e), exc_info=True)
        return JsonResponse({'error': 'Order could not be saved. Please try again.'}, status=409)

    return JsonResponse({
        'success': True,
        'order_id': order.id,
        'offline_id': offline_id,
    })


@login_required(login_url='waiter-login')
@require_POST
def api_update_order_status(request, order_id):
    """Update order status from offline sync queue."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    new_status = data.get('status')
    if new_status not in dict(Order.STATUS_CHOICES):
        return JsonResponse({'error': 'Invalid status'}, status=400)

    payment_method = data.get('payment_method', '')
    mpesa_code = data.get('mpesa_code', '')
    debtor = None

    if new_status == 'paid':
        if payment_method not in dict(Order.PAYMENT_CHOICES):
            return JsonResponse({'error': 'Invalid payment method'}, status=400)
        if payment_method == 'mpesa':
            mpesa_code = (mpesa_code or '').strip().upper()
            if len(mpesa_code) != 4 or not mpesa_code.isalnum():
                return JsonResponse({'error': 'M-Pesa code must be 4 alphanumeric characters'}, status=400)
        if payment_method == 'credit':
            debtor_id = data.get('debtor_id')
            if debtor_id:
                from debtor.models import Debtor
                try:
                    debtor = Debtor.objects.get(pk=debtor_id, is_active=True)
                except Debtor.DoesNotExist:
                    return JsonResponse({'error': 'Debtor not found'}, status=400)

    from django.db.models import Q
    with transaction.atomic():
        # Lock the row so a replayed offline-sync POST can't double-apply.
        base = Order.objects.select_for_update()
        if request.user.is_superuser or _is_supervisor(request.user):
            order = get_object_or_404(base, id=order_id)
        else:
            order = get_object_or_404(
                base, Q(waiter=request.user) | Q(created_by=request.user), id=order_id,
            )

        # Idempotency: a replayed sync for an order already in the target state
        # is a success no-op — so the client dequeues it instead of retrying
        # forever and double-crediting the account.
        if order.status == new_status:
            return JsonResponse({'success': True, 'order_id': order.id, 'idempotent': True})

        # Refund a paid order being cancelled during shift correction (mirrors
        # the web order_update_status view) — restore stock + reverse payment.
        refunding = (
            new_status == 'cancelled' and order.status == 'paid'
            and order.shift is not None and order.shift.in_correction
        )
        if refunding:
            blocked = _order_refund_blocked_reason(order)
            if blocked:
                return JsonResponse({'error': blocked}, status=400)

        if new_status == 'paid':
            order.payment_method = payment_method
            if payment_method == 'mpesa':
                order.mpesa_code = mpesa_code
            if payment_method == 'credit':
                order.debtor = debtor

        if new_status == 'cancelled' and order.status == 'active':
            _restore_order_stock(order)
        elif refunding:
            _restore_order_stock(order)
            _reverse_order_payment_for_correction(order, request.user)

        order.status = new_status
        order.save()
        if new_status == 'paid':
            _record_order_accounting(order, request.user)
        if new_status in ['paid', 'cancelled'] and order.table:
            order.table.status = 'available'
            order.table.save()

    return JsonResponse({'success': True, 'order_id': order.id})


@login_required(login_url='waiter-login')
@require_GET
def api_sync_status(request):
    """Return current server timestamp for sync coordination."""
    return JsonResponse({
        'server_time': timezone.now().isoformat(),
        'user': request.user.username,
        'has_shift': Shift.objects.filter(waiter=request.user, is_active=True).exists(),
    })
