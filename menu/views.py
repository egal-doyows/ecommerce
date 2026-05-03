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

from .models import Category, MenuItem, Table, Order, OrderItem, Shift, RestaurantSettings
from cart.cart import Cart


def _get_attendants():
    """Return active users in the Attendant group."""
    return User.objects.filter(
        groups__name='Attendant', is_active=True,
    ).order_by('username')


def _is_supervisor(user):
    """Return True if user is in the Supervisor group."""
    return user.groups.filter(name='Supervisor').exists()


def _is_promoter(user):
    """Return True if user is in the Promoter group (was 'Marketing' pre-2026)."""
    return user.groups.filter(name='Promoter').exists()


def _is_manager_or_above(user):
    """Return True if user is superuser, Owner, Manager, or Supervisor."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Owner', 'Manager', 'Supervisor']).exists()


def _must_select_attendant(user):
    """Return True if user must select an attendant when creating orders."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Owner', 'Manager', 'Supervisor', 'Promoter']).exists()


def _is_auto_shift_user(user):
    """Superusers, Owners, Managers, Supervisors, and Promoters get auto-created shifts (no starting cash).

    Servers and Cashiers must clock in manually.
    """
    if _is_manager_or_above(user):
        return True
    return _is_promoter(user)


def _ensure_shift(user):
    """Auto-create a shift if the user doesn't have one."""
    if not Shift.objects.filter(waiter=user, is_active=True).exists():
        Shift.objects.create(waiter=user, starting_cash=0)


def shift_required(view_func):
    """Redirect to shift page if user has no active shift.
    Superusers and Managers get an auto-created shift (no starting cash).
    Servers and Cashiers must clock in manually.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            if _is_auto_shift_user(request.user):
                _ensure_shift(request.user)
            elif not Shift.objects.filter(waiter=request.user, is_active=True).exists():
                return redirect('shift')
        return view_func(request, *args, **kwargs)
    return wrapper


def categories(request):
    all_categories = Category.objects.all()
    return {'all_categories': all_categories}


def restaurant_settings(request):
    return {'restaurant': RestaurantSettings.load()}


@never_cache
def service_worker_view(request):
    """Serve sw.js from root scope with correct content type."""
    sw_path = django_settings.BASE_DIR / 'static' / 'js' / 'sw.js'
    with open(sw_path, 'r') as f:
        return HttpResponse(f.read(), content_type='application/javascript')


def offline_view(request):
    """Offline fallback page."""
    return render(request, 'menu/offline.html')


@login_required(login_url='waiter-login')
@shift_required
def pos_home(request):
    # Order by category then title so the template's {% regroup %} produces
    # one section per category (alphabetical), each with its items sorted.
    all_products = (
        MenuItem.objects
        .filter(is_available=True)
        .select_related('category')
        .order_by('category__name', 'title')
    )
    tables = Table.objects.all()
    show_attendant_select = _must_select_attendant(request.user)
    context = {
        'all_products': all_products,
        'tables': tables,
        'show_attendant_select': show_attendant_select,
        'attendants': _get_attendants() if show_attendant_select else [],
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
    products = MenuItem.objects.filter(category=category, is_available=True)
    return render(request, 'menu/category-filter.html', {'products': products, 'category': category})


@login_required(login_url='waiter-login')
@shift_required
def place_order(request):
    if request.method == 'POST':
        cart = Cart(request)
        table_id = request.POST.get('table_id')
        order_notes = request.POST.get('notes', '')

        if not table_id or cart.__len__() == 0:
            return JsonResponse({'error': 'Select a table and add items'}, status=400)

        table = get_object_or_404(Table, id=table_id)

        # Determine who gets credit for the order (commission).
        # Managers, Supervisors, Superusers, and Marketing must select an attendant.
        # Promoter users are tracked as created_by and also earn commission.
        order_waiter = request.user
        order_created_by = None
        if _must_select_attendant(request.user):
            attendant_id = request.POST.get('attendant_id')
            if not attendant_id:
                return JsonResponse({'error': 'Select an attendant'}, status=400)
            order_waiter = get_object_or_404(
                User, id=attendant_id, groups__name='Attendant', is_active=True,
            )
            # Promoters earn commission on orders they create
            if _is_promoter(request.user):
                order_created_by = request.user

        active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()

        with transaction.atomic():
            order = Order.objects.create(
                table=table,
                waiter=order_waiter,
                created_by=order_created_by,
                shift=active_shift,
                notes=order_notes,
                status='active',
            )

            for item in cart:
                product = item['product']
                qty = item['qty']
                OrderItem.objects.create(
                    order=order,
                    menu_item=product,
                    quantity=qty,
                    unit_price=item['price'],
                    unit_cost=product.current_unit_cost(),
                )
                try:
                    product.deduct_stock(qty)
                except Exception:
                    logger.warning("Stock deduction failed for %s", product.title)

            table.status = 'occupied'
            table.save()

        cart.clear()

        return redirect('order-detail', order_id=order.id)

    return redirect('pos')


@login_required(login_url='waiter-login')
@shift_required
def order_detail(request, order_id):
    if request.user.is_superuser or _is_supervisor(request.user):
        order = get_object_or_404(Order, id=order_id)
    else:
        # Allow access if user is the waiter OR the creator (marketing)
        from django.db.models import Q
        order = get_object_or_404(
            Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id,
        )
    menu_items = MenuItem.objects.filter(is_available=True) if order.status == 'active' else []
    debtors = []
    if order.status == 'active':
        from debtor.models import Debtor
        debtors = Debtor.objects.filter(is_active=True)
    return render(request, 'menu/order-detail.html', {
        'order': order, 'menu_items': menu_items, 'debtors': debtors,
    })


@login_required(login_url='waiter-login')
@shift_required
def order_edit_item(request, order_id):
    """Add items to an active (unpaid) order."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if request.user.is_superuser or _is_supervisor(request.user):
        order = get_object_or_404(Order, id=order_id)
    else:
        from django.db.models import Q
        order = get_object_or_404(
            Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id,
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
            unit_cost=menu_item.current_unit_cost(),
        )

    return redirect('order-detail', order_id=order.id)


@login_required(login_url='waiter-login')
@shift_required
def order_list(request):
    base_qs = Order.objects.exclude(status='cancelled')
    if not (request.user.is_superuser or _is_supervisor(request.user)):
        from django.db.models import Q
        base_qs = base_qs.filter(Q(waiter=request.user) | Q(created_by=request.user))
    unpaid_orders = base_qs.filter(status='active')
    paid_orders = base_qs.filter(status='paid')
    total_unpaid = sum(o.get_total() for o in unpaid_orders)
    total_paid = sum(o.get_total() for o in paid_orders)
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
        if request.user.is_superuser or _is_supervisor(request.user):
            order = get_object_or_404(Order, id=order_id)
        else:
            from django.db.models import Q
            order = get_object_or_404(
                Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id,
            )
        new_status = request.POST.get('status')

        if new_status in dict(Order.STATUS_CHOICES):
            # Handle payment
            if new_status == 'paid':
                payment_method = request.POST.get('payment_method', '')
                if payment_method not in dict(Order.PAYMENT_CHOICES):
                    return redirect('order-detail', order_id=order.id)
                order.payment_method = payment_method
                if payment_method == 'mpesa':
                    mpesa_code = request.POST.get('mpesa_code', '').strip().upper()
                    if len(mpesa_code) != 4 or not mpesa_code.isalnum():
                        return redirect('order-detail', order_id=order.id)
                    order.mpesa_code = mpesa_code
                if payment_method == 'credit':
                    debtor_id = request.POST.get('debtor_id')
                    if not debtor_id:
                        return redirect('order-detail', order_id=order.id)
                    from debtor.models import Debtor
                    try:
                        order.debtor = Debtor.objects.get(pk=debtor_id, is_active=True)
                    except Debtor.DoesNotExist:
                        return redirect('order-detail', order_id=order.id)

            # Restore stock when cancelling an active order
            if new_status == 'cancelled' and order.status == 'active':
                for oi in order.items.select_related('menu_item').all():
                    try:
                        oi.menu_item.restore_stock(oi.quantity)
                    except Exception:
                        logger.warning("Stock restore failed for %s", oi.menu_item.title)

            order.status = new_status
            order.save()

            # Record payment in accounts
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

            if new_status in ['paid', 'cancelled']:
                if order.table:
                    order.table.status = 'available'
                    order.table.save()

        return redirect('order-detail', order_id=order.id)

    return redirect('order-list')


@login_required(login_url='waiter-login')
@shift_required
def tables_view(request):
    tables = Table.objects.all()
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
        table = get_object_or_404(Table, id=table_id)
        if table.status == 'available':
            table.status = 'reserved'
        elif table.status == 'reserved':
            table.status = 'available'
        table.save()
    return redirect('tables')


# ---- Shift views (no shift_required — this IS the shift page) ----

@login_required(login_url='waiter-login')
def shift_view(request):
    active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
    past_shifts = Shift.objects.filter(waiter=request.user, is_active=False)[:10]

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
        existing = Shift.objects.filter(waiter=request.user, is_active=True).first()
        if not existing:
            starting_cash = request.POST.get('starting_cash', '0')
            try:
                starting_cash = round(float(starting_cash), 2)
            except (ValueError, TypeError):
                starting_cash = 0
            Shift.objects.create(waiter=request.user, starting_cash=starting_cash)
    return redirect('shift')


@login_required(login_url='waiter-login')
def shift_clock_out(request):
    if request.method == 'POST':
        shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
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
    shift = get_object_or_404(Shift, id=shift_id, waiter=request.user)
    orders = shift.orders.all()
    context = {
        'shift': shift,
        'orders': orders,
    }
    return render(request, 'menu/shift-detail.html', context)
