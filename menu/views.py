import logging
from functools import wraps

from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

from django.contrib.auth.models import User

from .models import Category, MenuItem, Table, Order, OrderItem, Shift, RestaurantSettings, BranchMenuAvailability, Station, StationRequest
from .services import place_order as service_place_order, update_order_status as service_update_order_status, InvalidTransition
from cart.cart import Cart


def _get_attendants(branch=None):
    """Return active users in the Attendant group, optionally filtered by branch."""
    qs = User.objects.filter(
        groups__name='Attendant', is_active=True,
    )
    if branch:
        from branches.models import UserBranch
        branch_user_ids = UserBranch.objects.filter(branch=branch).values_list('user_id', flat=True)
        qs = qs.filter(id__in=branch_user_ids)
    return qs.order_by('username')


from core.permissions import (
    OWNER, OVERALL_MANAGER, BRANCH_MANAGER, SUPERVISOR, MARKETING,
    ADMIN_GROUPS,
)


def _is_supervisor(user):
    """Return True if user is in the Supervisor group."""
    return user.groups.filter(name=SUPERVISOR).exists()


def _is_marketing(user):
    """Return True if user is in the Marketing group."""
    return user.groups.filter(name=MARKETING).exists()


def _is_manager_or_above(user):
    """Return True if user is superuser, Owner, Branch Manager, or Supervisor.
    Overall Managers are excluded — they don't work shifts."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=[OWNER, BRANCH_MANAGER, SUPERVISOR]).exists()


def _must_select_attendant(user):
    """Return True if user must select an attendant when creating orders."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=[OWNER, BRANCH_MANAGER, OVERALL_MANAGER, SUPERVISOR, MARKETING]).exists()


def _is_auto_shift_user(user):
    """Superusers, Managers, Supervisors, and Marketing get auto-created shifts (no starting cash)."""
    if _is_manager_or_above(user):
        return True
    return _is_marketing(user)


def _ensure_shift(user, branch=None):
    """Auto-create a shift if the user doesn't have one."""
    if not Shift.objects.filter(waiter=user, is_active=True, branch=branch).exists():
        Shift.objects.create(waiter=user, starting_cash=0, branch=branch)


def shift_required(view_func):
    """Redirect to shift page if user has no active shift.
    Superusers and Managers get an auto-created shift (no starting cash).
    Front Service and Cashiers must clock in manually.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            if _is_auto_shift_user(request.user):
                _ensure_shift(request.user, branch=getattr(request, 'branch', None))
            elif not Shift.objects.filter(waiter=request.user, is_active=True, branch=getattr(request, 'branch', None)).exists():
                return redirect('shift')
        return view_func(request, *args, **kwargs)
    return wrapper


def categories(request):
    all_categories = Category.objects.all()
    return {'all_categories': all_categories}


def restaurant_settings(request):
    return {'restaurant': RestaurantSettings.load()}


def waiter_notifications(request):
    """Context processor: counts of ready items and pending station requests for the logged-in waiter."""
    if not request.user.is_authenticated:
        return {}
    # Skip for station display users
    if request.user.groups.filter(name='Display').exists():
        return {}
    from django.db.models import Q
    user = request.user
    branch = getattr(request, 'branch', None)

    # Items marked ready on waiter's active orders (not yet acknowledged)
    ready_qs = OrderItem.objects.filter(
        order__status='active',
        preparation_status='ready',
        ready_acknowledged=False,
    ).filter(Q(order__waiter=user) | Q(order__created_by=user))
    if branch:
        ready_qs = ready_qs.filter(order__branch=branch)
    ready_count = ready_qs.count()

    # Pending station requests (edit/cancel) on waiter's orders
    request_qs = StationRequest.objects.filter(
        status='pending',
        order_item__order__status='active',
    ).filter(
        Q(order_item__order__waiter=user) | Q(order_item__order__created_by=user)
    )
    if branch:
        request_qs = request_qs.filter(order_item__order__branch=branch)
    station_request_count = request_qs.count()

    total = ready_count + station_request_count
    return {
        'waiter_ready_count': ready_count,
        'waiter_request_count': station_request_count,
        'waiter_notification_total': total,
    }


@never_cache
def service_worker_view(request):
    """Serve sw.js from root scope with correct content type."""
    sw_path = django_settings.BASE_DIR / 'static' / 'js' / 'sw.js'
    try:
        with open(sw_path, 'r') as f:
            return HttpResponse(f.read(), content_type='application/javascript')
    except FileNotFoundError:
        return HttpResponse('// Service worker not found', content_type='application/javascript', status=404)


def offline_view(request):
    """Offline fallback page."""
    return render(request, 'menu/offline.html')


def _get_available_items(branch):
    """Return MenuItems available at a specific branch.

    Uses per-branch overrides when they exist, otherwise falls back
    to the global is_available flag.
    """
    if not branch:
        return MenuItem.objects.filter(is_available=True)

    # Items explicitly disabled for this branch
    disabled_ids = set(
        BranchMenuAvailability.objects
        .filter(branch=branch, is_available=False)
        .values_list('menu_item_id', flat=True)
    )
    # Items explicitly enabled for this branch (even if globally off)
    enabled_ids = set(
        BranchMenuAvailability.objects
        .filter(branch=branch, is_available=True)
        .values_list('menu_item_id', flat=True)
    )
    # Globally available minus disabled, plus explicitly enabled
    global_available = set(
        MenuItem.objects.filter(is_available=True).values_list('id', flat=True)
    )
    final_ids = (global_available - disabled_ids) | enabled_ids
    return MenuItem.objects.filter(pk__in=final_ids)


def _is_station_user(user):
    """Return True if user is in the Display group."""
    return user.groups.filter(name='Display').exists()


@login_required(login_url='waiter-login')
@shift_required
def pos_home(request):
    # Kitchen / Barister staff should only see their station display
    if _is_station_user(request.user):
        return redirect('station-display')
    # Overall Managers should not create orders — they only oversee branches
    if not request.user.is_superuser and request.user.groups.filter(name='Overall Manager').exists():
        from django.contrib import messages
        messages.error(request, 'Overall Managers cannot create orders. Use the admin panel to manage branches.')
        return redirect('admin-dashboard')
    all_products = _get_available_items(request.branch)
    tables = Table.objects.filter(branch=request.branch)
    show_attendant_select = _must_select_attendant(request.user)
    context = {
        'all_products': all_products,
        'tables': tables,
        'show_attendant_select': show_attendant_select,
        'attendants': _get_attendants(request.branch) if show_attendant_select else [],
    }
    return render(request, 'menu/pos.html', context)


@login_required(login_url='waiter-login')
@shift_required
def item_detail(request, slug):
    product = get_object_or_404(MenuItem, slug=slug)
    context = {'product': product}
    return render(request, 'menu/item-detail.html', context)


@login_required(login_url='waiter-login')
@shift_required
def category_filter(request, category_slug):
    category = get_object_or_404(Category, slug=category_slug)
    products = _get_available_items(request.branch).filter(category=category)
    return render(request, 'menu/category-filter.html', {'products': products, 'category': category})


@login_required(login_url='waiter-login')
@shift_required
def place_order(request):
    if request.method == 'POST':
        cart = Cart(request)
        table_id = request.POST.get('table_id')
        order_notes = request.POST.get('notes', '')

        if not table_id or cart.__len__() == 0:
            from django.contrib import messages
            messages.error(request, 'Please select a space and add items before placing an order.')
            return redirect('pos')

        table = get_object_or_404(Table, id=table_id, branch=request.branch)

        # Determine who gets credit for the order (commission).
        order_waiter = request.user
        order_created_by = None
        if _must_select_attendant(request.user):
            attendant_id = request.POST.get('attendant_id')
            if not attendant_id:
                from django.contrib import messages
                messages.error(request, 'Please select an attendant.')
                return redirect('pos')
            order_waiter = get_object_or_404(
                User, id=attendant_id, groups__name='Attendant', is_active=True,
            )
            if _is_marketing(request.user):
                order_created_by = request.user

        active_shift = Shift.objects.filter(waiter=request.user, is_active=True, branch=request.branch).first()

        # Build cart items for service layer
        cart_items = []
        for item in cart:
            cart_items.append({
                'product': item['product'],
                'qty': item['qty'],
                'price': item['price'],
            })

        try:
            order = service_place_order(
                cart_items=cart_items,
                table=table,
                waiter=order_waiter,
                created_by=order_created_by,
                shift=active_shift,
                notes=order_notes,
                branch=request.branch,
            )
        except Exception as e:
            from django.contrib import messages
            logger.warning("Order placement failed: %s", str(e))
            messages.error(request, f'Order failed: {e}')
            return redirect('pos')

        cart.clear()
        return redirect('order-detail', order_id=order.id)

    return redirect('pos')


@login_required(login_url='waiter-login')
@shift_required
def order_detail(request, order_id):
    if request.user.is_superuser or _is_supervisor(request.user) or _is_manager_or_above(request.user):
        order = get_object_or_404(Order, id=order_id, branch=request.branch)
    else:
        # Allow access if user is the waiter OR the creator (marketing)
        from django.db.models import Q
        order = get_object_or_404(
            Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id, branch=request.branch,
        )
    menu_items = _get_available_items(request.branch) if order.status == 'active' else []
    debtors = []
    if order.status == 'active':
        from debtor.models import Debtor
        debtors = Debtor.objects.filter(is_active=True, branch=request.branch)

    # Pending station requests for this order
    pending_requests = StationRequest.objects.filter(
        order_item__order=order,
        status='pending',
    ).select_related('order_item__menu_item', 'requested_by')

    return render(request, 'menu/order-detail.html', {
        'order': order, 'menu_items': menu_items, 'debtors': debtors,
        'pending_requests': pending_requests,
    })


@login_required(login_url='waiter-login')
@shift_required
def order_edit_item(request, order_id):
    """Add items to an active (unpaid) order."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if request.user.is_superuser or _is_supervisor(request.user) or _is_manager_or_above(request.user):
        order = get_object_or_404(Order, id=order_id, branch=request.branch)
    else:
        from django.db.models import Q
        order = get_object_or_404(
            Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id, branch=request.branch,
        )

    if order.status != 'active':
        return JsonResponse({'error': 'Can only edit unpaid orders'}, status=400)

    menu_item_id = request.POST.get('menu_item_id')
    if not menu_item_id:
        return redirect('order-detail', order_id=order.id)

    menu_item = get_object_or_404(MenuItem, id=menu_item_id, is_available=True)

    # Check if item already in order — increase quantity
    existing = order.items.filter(menu_item=menu_item).first()
    if existing:
        try:
            menu_item.deduct_stock(1)
        except Exception:
            logger.warning("Stock deduction failed for %s", menu_item.title)
        existing.quantity += 1
        existing.save()
    else:
        try:
            menu_item.deduct_stock(1)
        except Exception:
            logger.warning("Stock deduction failed for %s", menu_item.title)
        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=1,
            unit_price=menu_item.price,
        )

    return redirect('order-detail', order_id=order.id)


@login_required(login_url='waiter-login')
@shift_required
def order_list(request):
    base_qs = Order.objects.filter(branch=request.branch).exclude(status='cancelled')
    if not (request.user.is_superuser or _is_supervisor(request.user)):
        from django.db.models import Q
        base_qs = base_qs.filter(Q(waiter=request.user) | Q(created_by=request.user))
    from django.db.models import Sum as DSum, F as DF, Count, Q as DQ
    unpaid_base = base_qs.filter(status='active')
    unpaid_orders = unpaid_base.annotate(
        ready_count=Count('items', filter=DQ(items__preparation_status='ready')),
        pending_request_count=Count(
            'items__station_requests',
            filter=DQ(items__station_requests__status='pending'),
        ),
    )
    paid_orders = base_qs.filter(status='paid')
    # Compute totals from un-annotated querysets to avoid join multiplication
    total_unpaid = unpaid_base.aggregate(
        total=DSum(DF('items__unit_price') * DF('items__quantity'))
    )['total'] or 0
    total_paid = paid_orders.aggregate(
        total=DSum(DF('items__unit_price') * DF('items__quantity'))
    )['total'] or 0
    context = {
        'unpaid_orders': unpaid_orders,
        'paid_orders': paid_orders,
        'total_unpaid': total_unpaid,
        'total_paid': total_paid,
    }
    return render(request, 'menu/order-list.html', context)


@login_required(login_url='waiter-login')
@shift_required
def order_update_status(request, order_id):
    if request.method == 'POST':
        if request.user.is_superuser or _is_supervisor(request.user) or _is_manager_or_above(request.user):
            order = get_object_or_404(Order, id=order_id, branch=request.branch)
        else:
            from django.db.models import Q
            order = get_object_or_404(
                Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id, branch=request.branch,
            )
        new_status = request.POST.get('status')

        if new_status not in dict(Order.STATUS_CHOICES):
            return redirect('order-detail', order_id=order.id)

        # Validate payment details before attempting transition
        payment_method = ''
        mpesa_code = ''
        debtor = None

        if new_status == 'paid':
            payment_method = request.POST.get('payment_method', '')
            if payment_method not in dict(Order.PAYMENT_CHOICES):
                return redirect('order-detail', order_id=order.id)
            if payment_method == 'mpesa':
                mpesa_code = request.POST.get('mpesa_code', '').strip().upper()
                if len(mpesa_code) != 4 or not mpesa_code.isalnum():
                    return redirect('order-detail', order_id=order.id)
            if payment_method == 'credit':
                debtor_id = request.POST.get('debtor_id')
                if not debtor_id:
                    return redirect('order-detail', order_id=order.id)
                from debtor.models import Debtor
                try:
                    debtor = Debtor.objects.get(pk=debtor_id, is_active=True, branch=request.branch)
                except Debtor.DoesNotExist:
                    return redirect('order-detail', order_id=order.id)

        try:
            service_update_order_status(
                order, new_status,
                payment_method=payment_method,
                mpesa_code=mpesa_code,
                debtor=debtor,
                user=request.user,
            )
        except InvalidTransition as e:
            from django.contrib import messages
            messages.error(request, str(e))

        return redirect('order-detail', order_id=order.id)

    return redirect('order-list')


@login_required(login_url='waiter-login')
@shift_required
def tables_view(request):
    tables = Table.objects.filter(branch=request.branch)
    context = {
        'tables': tables,
        'available_count': tables.filter(status='available').count(),
        'occupied_count': tables.filter(status='occupied').count(),
        'reserved_count': tables.filter(status='reserved').count(),
    }
    return render(request, 'menu/tables.html', context)


@login_required(login_url='waiter-login')
@shift_required
def table_toggle_reserve(request, table_id):
    if request.method == 'POST':
        table = get_object_or_404(Table, id=table_id, branch=request.branch)
        if table.status == 'available':
            table.status = 'reserved'
        elif table.status == 'reserved':
            table.status = 'available'
        table.save()
    return redirect('tables')


# ---- Shift views (no shift_required — this IS the shift page) ----

@login_required(login_url='waiter-login')
def shift_view(request):
    active_shift = Shift.objects.filter(waiter=request.user, is_active=True, branch=request.branch).first()
    past_shifts = Shift.objects.filter(waiter=request.user, is_active=False, branch=request.branch)[:10]

    unpaid_orders = []
    if active_shift:
        unpaid_orders = active_shift.orders.filter(status='active')

    context = {
        'active_shift': active_shift,
        'past_shifts': past_shifts,
        'unpaid_orders': unpaid_orders,
    }
    return render(request, 'menu/shift.html', context)


@login_required(login_url='waiter-login')
def shift_clock_in(request):
    if request.method == 'POST':
        existing = Shift.objects.filter(waiter=request.user, is_active=True, branch=request.branch).first()
        if not existing:
            starting_cash = request.POST.get('starting_cash', '0')
            try:
                starting_cash = round(float(starting_cash), 2)
            except (ValueError, TypeError):
                starting_cash = 0
            Shift.objects.create(waiter=request.user, starting_cash=starting_cash, branch=request.branch)
    return redirect('shift')


@login_required(login_url='waiter-login')
def shift_clock_out(request):
    if request.method == 'POST':
        shift = Shift.objects.filter(waiter=request.user, is_active=True, branch=request.branch).first()
        if shift:
            unpaid = shift.orders.filter(status='active').count()
            if unpaid:
                return redirect('shift')
            shift.ended_at = timezone.now()
            shift.is_active = False
            shift.save()
        # Managers/Supervisors stay logged in → admin dashboard
        if _is_auto_shift_user(request.user):
            return redirect('admin-dashboard')
        from django.contrib.auth import logout
        logout(request)
    return redirect('waiter-login')


@login_required(login_url='waiter-login')
def shift_detail(request, shift_id):
    shift = get_object_or_404(Shift, id=shift_id, waiter=request.user, branch=request.branch)
    orders = shift.orders.all()
    context = {
        'shift': shift,
        'orders': orders,
    }
    return render(request, 'menu/shift-detail.html', context)


# ---- Station Display (Kitchen / Bar) ----

def _get_station_orders(station, branch=None):
    """Return orders with pending items for the given station."""
    from django.db.models import Exists, OuterRef

    orders = Order.objects.filter(
        status='active',
        items__menu_item__category__station=station,
    ).distinct().select_related('table', 'waiter')

    if branch:
        orders = orders.filter(branch=branch)

    orders = orders.order_by('created_at')

    result = []
    for order in orders:
        pending_items = order.items.filter(
            menu_item__category__station=station,
        ).select_related('menu_item', 'menu_item__category').exclude(
            preparation_status='ready',
        ).annotate(
            has_pending_request=Exists(
                StationRequest.objects.filter(
                    order_item=OuterRef('pk'),
                    status='pending',
                )
            ),
        )
        if not pending_items.exists():
            continue
        has_pending = any(item.has_pending_request for item in pending_items)
        result.append({
            'order': order,
            'items': pending_items,
            'has_pending_request': has_pending,
        })
    return result


@login_required(login_url='waiter-login')
def station_display(request):
    """Live order display with tabs for Kitchen and Bar."""
    if not _is_station_user(request.user):
        return redirect('pos')

    branch = getattr(request, 'branch', None)
    stations = Station.objects.all()
    active_tab = request.GET.get('tab', '')

    tabs = []
    for station in stations:
        orders = _get_station_orders(station, branch)
        tabs.append({
            'station': station,
            'orders': orders,
            'count': len(orders),
        })

    # Default to first tab with orders, or first tab
    if not active_tab and tabs:
        active_tab = tabs[0]['station'].name

    return render(request, 'menu/station_display.html', {
        'tabs': tabs,
        'active_tab': active_tab,
    })


@login_required(login_url='waiter-login')
def station_api_orders(request):
    """JSON endpoint for polling — returns current orders for a given station."""
    if not _is_station_user(request.user):
        return JsonResponse({'error': 'Access denied'}, status=403)

    station_id = request.GET.get('station')
    station = get_object_or_404(Station, id=station_id) if station_id else None
    if not station:
        return JsonResponse({'orders': []})

    branch = getattr(request, 'branch', None)
    orders = _get_station_orders(station, branch)

    data = []
    for entry in orders:
        order = entry['order']
        items = entry['items']
        data.append({
            'id': order.id,
            'order_number': order.order_number or f'#{order.id}',
            'waiter': order.waiter.username if order.waiter else 'N/A',
            'created_at': order.created_at.isoformat(),
            'notes': order.notes,
            'has_pending_request': entry['has_pending_request'],
            'items': [
                {
                    'id': item.id,
                    'name': item.menu_item.title,
                    'quantity': item.quantity,
                    'notes': item.notes,
                    'status': item.preparation_status,
                    'has_pending_request': item.has_pending_request,
                }
                for item in items
            ],
        })

    return JsonResponse({'orders': data})


@login_required(login_url='waiter-login')
def station_update_item(request):
    """Update preparation status of an order item (kitchen/bar marks ready)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _is_station_user(request.user):
        return JsonResponse({'error': 'Access denied'}, status=403)

    item_id = request.POST.get('item_id')
    new_status = request.POST.get('status')

    if new_status not in ('preparing', 'ready'):
        return JsonResponse({'error': 'Invalid status'}, status=400)

    item = get_object_or_404(
        OrderItem,
        id=item_id,
        menu_item__category__station__isnull=False,
        order__status='active',
    )
    item.preparation_status = new_status
    item.save(update_fields=['preparation_status'])

    return JsonResponse({'ok': True, 'status': new_status})


@login_required(login_url='waiter-login')
def station_create_request(request):
    """Kitchen/bar staff request an edit or cancellation from the waiter."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _is_station_user(request.user):
        return JsonResponse({'error': 'Access denied'}, status=403)

    item_id = request.POST.get('item_id')
    request_type = request.POST.get('request_type')
    message = request.POST.get('message', '').strip()

    if request_type not in ('edit', 'cancel'):
        return JsonResponse({'error': 'Invalid request type'}, status=400)
    if not message:
        return JsonResponse({'error': 'Message is required'}, status=400)

    item = get_object_or_404(
        OrderItem,
        id=item_id,
        menu_item__category__station__isnull=False,
        order__status='active',
    )

    StationRequest.objects.create(
        order_item=item,
        request_type=request_type,
        message=message,
        requested_by=request.user,
    )

    return JsonResponse({'ok': True})


@login_required(login_url='waiter-login')
@shift_required
def acknowledge_ready_items(request):
    """Waiter acknowledges all ready items — clears them from the bell notification."""
    if request.method != 'POST':
        return redirect('order-list')
    from django.db.models import Q
    user = request.user
    branch = getattr(request, 'branch', None)
    qs = OrderItem.objects.filter(
        order__status='active',
        preparation_status='ready',
        ready_acknowledged=False,
    ).filter(Q(order__waiter=user) | Q(order__created_by=user))
    if branch:
        qs = qs.filter(order__branch=branch)
    qs.update(ready_acknowledged=True)
    return redirect('order-list')


@login_required(login_url='waiter-login')
@shift_required
def respond_station_request(request, request_id):
    """Waiter accepts or rejects a station request."""
    if request.method != 'POST':
        return redirect('order-list')

    from django.db.models import Q
    sr = get_object_or_404(
        StationRequest,
        id=request_id,
        status='pending',
    )
    order = sr.order_item.order
    # Only the waiter/creator of the order can respond
    if not (request.user.is_superuser or _is_supervisor(request.user) or _is_manager_or_above(request.user)):
        if order.waiter != request.user and order.created_by != request.user:
            from django.contrib import messages
            messages.error(request, 'You cannot respond to this request.')
            return redirect('order-detail', order_id=order.id)

    action = request.POST.get('action')  # 'accept' or 'reject'
    if action == 'accept':
        sr.status = 'accepted'
        sr.responded_by = request.user
        sr.responded_at = timezone.now()
        sr.save(update_fields=['status', 'responded_by', 'responded_at'])
        # If cancel request was accepted, remove the item from the order
        if sr.request_type == 'cancel':
            item = sr.order_item
            try:
                item.menu_item.restore_stock(item.quantity)
            except Exception:
                pass
            item.delete()
    elif action == 'reject':
        sr.status = 'rejected'
        sr.responded_by = request.user
        sr.responded_at = timezone.now()
        sr.save(update_fields=['status', 'responded_by', 'responded_at'])

    return redirect('order-detail', order_id=order.id)
