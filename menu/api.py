"""JSON API endpoints for PWA offline support."""
import json
import logging

from django.db import transaction
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Category, MenuItem, Table, Order, OrderItem, Shift
from .views import (
    _must_select_attendant, _is_supervisor, _is_marketing,
    _is_auto_shift_user, _ensure_shift, _is_manager_or_above,
)

logger = logging.getLogger(__name__)


@login_required(login_url='waiter-login')
@require_GET
def api_menu(request):
    """Return all available menu items with categories."""
    categories = list(Category.objects.values('id', 'name', 'slug', 'icon'))
    items = list(
        MenuItem.objects.filter(is_available=True).values(
            'id', 'title', 'slug', 'description', 'price',
            'category_id', 'image', 'item_tier', 'preparation_time',
        )
    )
    for item in items:
        item['price'] = float(item['price'])
        if item['image']:
            item['image'] = f'/media/{item["image"]}'
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
    attendant_id = data.get('attendant_id')
    offline_id = data.get('offline_id', '')

    if not table_id or not items:
        return JsonResponse({'error': 'Table and items required'}, status=400)

    table = get_object_or_404(Table, id=table_id)

    # Ensure shift exists
    if _is_auto_shift_user(request.user):
        _ensure_shift(request.user)

    active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
    if not active_shift:
        return JsonResponse({'error': 'No active shift'}, status=400)

    order_waiter = request.user
    order_created_by = None
    if _must_select_attendant(request.user):
        if not attendant_id:
            return JsonResponse({'error': 'Select an attendant'}, status=400)
        order_waiter = get_object_or_404(
            User, id=attendant_id, groups__name='Attendant', is_active=True,
        )
        if _is_marketing(request.user):
            order_created_by = request.user

    with transaction.atomic():
        order = Order.objects.create(
            table=table,
            waiter=order_waiter,
            created_by=order_created_by,
            shift=active_shift,
            notes=notes,
            status='active',
        )
        for cart_item in items:
            menu_item = get_object_or_404(MenuItem, id=cart_item['id'])
            qty = int(cart_item.get('qty', 1))
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=qty,
                unit_price=menu_item.price,
            )
            try:
                menu_item.deduct_stock(qty)
            except Exception:
                logger.warning("Stock deduction failed for %s", menu_item.title)

        table.status = 'occupied'
        table.save()

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

    if request.user.is_superuser or _is_supervisor(request.user):
        order = get_object_or_404(Order, id=order_id)
    else:
        from django.db.models import Q
        order = get_object_or_404(
            Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id,
        )

    new_status = data.get('status')
    if new_status not in dict(Order.STATUS_CHOICES):
        return JsonResponse({'error': 'Invalid status'}, status=400)

    if new_status == 'paid':
        payment_method = data.get('payment_method', '')
        if payment_method not in dict(Order.PAYMENT_CHOICES):
            return JsonResponse({'error': 'Invalid payment method'}, status=400)
        order.payment_method = payment_method
        if payment_method == 'mpesa':
            order.mpesa_code = data.get('mpesa_code', '')
        if payment_method == 'credit':
            debtor_id = data.get('debtor_id')
            if debtor_id:
                from debtor.models import Debtor
                try:
                    order.debtor = Debtor.objects.get(pk=debtor_id, is_active=True)
                except Debtor.DoesNotExist:
                    return JsonResponse({'error': 'Debtor not found'}, status=400)

    if new_status == 'cancelled' and order.status == 'active':
        for oi in order.items.select_related('menu_item').all():
            try:
                oi.menu_item.restore_stock(oi.quantity)
            except Exception:
                logger.warning("Stock restore failed for %s", oi.menu_item.title)

    order.status = new_status
    order.save()

    if new_status == 'paid':
        if order.payment_method == 'credit':
            from debtor.models import DebtorTransaction
            DebtorTransaction.objects.create(
                debtor=order.debtor,
                transaction_type='debit',
                amount=order.get_total(),
                description=f'Order #{order.id} — Space {order.table.number if order.table else "N/A"}',
                reference=str(order.id),
                created_by=request.user,
            )
        else:
            from administration.models import record_order_payment
            record_order_payment(order, created_by=request.user)

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
