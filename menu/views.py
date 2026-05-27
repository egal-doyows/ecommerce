import logging
from decimal import Decimal
from functools import wraps

from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

from .models import (
    Category, MenuItem, Table, Order, OrderItem, Shift, RestaurantSettings,
    AccompanimentOption, OrderItemOption,
)
from cart.cart import Cart


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
    from .cache import get_categories
    return {'all_categories': get_categories()}


def restaurant_settings(request):
    from .cache import get_restaurant_settings
    return {'restaurant': get_restaurant_settings()}


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
        .prefetch_related('accompaniment_groups__options')
        .order_by('category__name', 'title')
    )
    tables = Table.objects.all()
    context = {
        'all_products': all_products,
        'tables': tables,
        'accompaniments': _build_accompaniments(all_products),
    }
    return render(request, 'menu/pos.html', context)


def _build_accompaniments(products):
    """
    Per-product accompaniment data for the "choose a side" modal, keyed by
    product id: {pid: [{id, name, required, options: [{id, label, delta}]}]}.
    Only products with at least one group of available options are included.
    Pass a queryset that prefetches 'accompaniment_groups__options'.
    """
    accompaniments = {}
    for p in products:
        groups = []
        for g in p.accompaniment_groups.all():
            opts = [
                {'id': o.id, 'label': o.label, 'delta': str(o.price_delta)}
                for o in g.options.all() if o.is_available
            ]
            if opts:
                groups.append({
                    'id': g.id, 'name': g.name,
                    'required': g.is_required, 'options': opts,
                })
        if groups:
            accompaniments[p.id] = groups
    return accompaniments


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
        order_type = request.POST.get('order_type', 'dine_in')
        source = request.POST.get('source', 'pos')

        if order_type not in dict(Order.ORDER_TYPE_CHOICES):
            order_type = 'dine_in'
        if source not in dict(Order.SOURCE_CHOICES):
            source = 'pos'

        if cart.__len__() == 0:
            return JsonResponse({'error': 'Add items to the order'}, status=400)
        if order_type == 'dine_in' and not table_id:
            return JsonResponse({'error': 'Select a table for dine-in orders'}, status=400)

        table = get_object_or_404(Table, id=table_id) if table_id and order_type == 'dine_in' else None

        order_waiter = request.user
        order_created_by = None

        active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()

        with transaction.atomic():
            order = Order.objects.create(
                table=table,
                order_type=order_type,
                source=source,
                waiter=order_waiter,
                created_by=order_created_by,
                shift=active_shift,
                notes=order_notes,
                status='active',
            )

            for item in cart:
                product = item['product']
                qty = item['qty']
                options = item.get('options', [])

                option_objs = {}
                if options:
                    option_objs = {
                        o.id: o for o in AccompanimentOption.objects.filter(
                            id__in=[opt['id'] for opt in options],
                        )
                    }

                option_cost = sum(
                    (option_objs[opt['id']].current_unit_cost()
                     for opt in options if opt['id'] in option_objs),
                    Decimal('0'),
                )

                order_item = OrderItem.objects.create(
                    order=order,
                    menu_item=product,
                    quantity=qty,
                    unit_price=item['price'],  # all-in (base + option deltas)
                    unit_cost=product.current_unit_cost() + option_cost,
                )

                for opt in options:
                    obj = option_objs.get(opt['id'])
                    OrderItemOption.objects.create(
                        order_item=order_item,
                        option=obj,
                        group_name=opt.get('group_name', ''),
                        label=opt['label'],
                        price_delta=Decimal(str(opt['delta'])),
                        unit_cost=obj.current_unit_cost() if obj else Decimal('0'),
                    )

                try:
                    product.deduct_stock(qty)
                    for opt in options:
                        obj = option_objs.get(opt['id'])
                        if obj:
                            obj.deduct_stock(qty)
                except Exception:
                    logger.warning("Stock deduction failed for %s", product.title)

            if table:
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
    menu_items = (
        MenuItem.objects.filter(is_available=True)
        .prefetch_related('accompaniment_groups__options')
        if order.status == 'active' else []
    )
    debtors = []
    if order.status == 'active':
        from debtor.models import Debtor
        debtors = Debtor.objects.filter(is_active=True)
    return render(request, 'menu/order-detail.html', {
        'order': order, 'menu_items': menu_items, 'debtors': debtors,
        'accompaniments': _build_accompaniments(menu_items) if menu_items else {},
        'can_void': _can_void(request.user),
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

    from cart.views import resolve_options
    options, error = resolve_options(menu_item, request.POST.getlist('option_id'))
    if error:
        messages.error(request, error)
        return redirect('order-detail', order_id=order.id)

    with transaction.atomic():
        # Option-less items merge into an existing matching line; items with
        # accompaniments always become their own line (different choices = different line).
        existing = None
        if not options:
            existing = order.items.filter(menu_item=menu_item, options__isnull=True).first()

        option_objs = {}
        if options:
            option_objs = {
                o.id: o for o in AccompanimentOption.objects.filter(
                    id__in=[opt['id'] for opt in options],
                )
            }
        option_cost = sum(
            (option_objs[opt['id']].current_unit_cost()
             for opt in options if opt['id'] in option_objs),
            Decimal('0'),
        )
        option_delta = sum((Decimal(str(opt['delta'])) for opt in options), Decimal('0'))

        if existing:
            existing.quantity += 1
            existing.save()
        else:
            order_item = OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=1,
                unit_price=menu_item.price + option_delta,
                unit_cost=menu_item.current_unit_cost() + option_cost,
            )
            for opt in options:
                obj = option_objs.get(opt['id'])
                OrderItemOption.objects.create(
                    order_item=order_item,
                    option=obj,
                    group_name=opt.get('group_name', ''),
                    label=opt['label'],
                    price_delta=Decimal(str(opt['delta'])),
                    unit_cost=obj.current_unit_cost() if obj else Decimal('0'),
                )

        try:
            menu_item.deduct_stock(1)
            for opt in options:
                obj = option_objs.get(opt['id'])
                if obj:
                    obj.deduct_stock(1)
        except Exception:
            logger.warning("Stock deduction failed for %s", menu_item.title)

    return redirect('order-detail', order_id=order.id)


@login_required(login_url='waiter-login')
@shift_required
def order_list(request):
    from decimal import Decimal
    from django.core.paginator import Paginator
    from django.db.models import Q, Sum, F, DecimalField, Value
    from django.db.models.functions import Coalesce

    base_qs = Order.objects.exclude(status='cancelled')
    if not (request.user.is_superuser or _is_supervisor(request.user)):
        base_qs = base_qs.filter(Q(waiter=request.user) | Q(created_by=request.user))

    def _total(qs):
        """Sum unit_price * quantity across all OrderItems in qs — DB-side."""
        zero = Value(Decimal('0'), output_field=DecimalField(max_digits=12, decimal_places=2))
        return qs.aggregate(
            t=Coalesce(
                Sum(
                    F('items__unit_price') * F('items__quantity'),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                zero,
            ),
        )['t']

    unpaid_qs = base_qs.filter(status='active')

    # Identify which credit orders are still unsettled (invoice.remaining > 0).
    # Credit-paid orders that have been fully paid down move into "paid".
    from debtor.models import DebtorTransaction
    credit_paid_ids = list(
        base_qs.filter(status='paid', payment_method='credit').values_list('id', flat=True)
    )
    invoices_by_order = {}
    if credit_paid_ids:
        for inv in DebtorTransaction.objects.filter(
            transaction_type='debit',
            reference__in=[str(i) for i in credit_paid_ids],
        ):
            try:
                invoices_by_order[int(inv.reference)] = inv
            except (TypeError, ValueError):
                continue
    unsettled_ids = [oid for oid, inv in invoices_by_order.items() if inv.remaining > 0]

    credit_qs = base_qs.filter(id__in=unsettled_ids)
    paid_qs = base_qs.filter(status='paid').exclude(id__in=unsettled_ids)

    counts = {
        'unpaid': unpaid_qs.count(),
        'paid': paid_qs.count(),
        'credit': len(unsettled_ids),
    }
    total_unpaid = _total(unpaid_qs)
    total_paid = _total(paid_qs)
    total_credit = sum(
        (invoices_by_order[oid].remaining for oid in unsettled_ids),
        Decimal('0'),
    )

    show = request.GET.get('show', 'unpaid')
    if show not in ('unpaid', 'paid', 'credit'):
        show = 'unpaid'

    if show == 'unpaid':
        active_qs = unpaid_qs
    elif show == 'paid':
        active_qs = paid_qs
    else:
        active_qs = credit_qs

    active_qs = (
        active_qs
        .select_related('waiter', 'table', 'debtor')
        .prefetch_related('items__menu_item')
    )

    paginator = Paginator(active_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    if show == 'credit':
        for o in page_obj.object_list:
            inv = invoices_by_order.get(o.id)
            o.credit_remaining = inv.remaining if inv else Decimal('0')

    context = {
        'show': show,
        'page_obj': page_obj,
        'counts': counts,
        'total_unpaid': total_unpaid,
        'total_paid': total_paid,
        'total_credit': total_credit,
    }
    return render(request, 'menu/order-list.html', context)


def _can_void(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )


@login_required(login_url='waiter-login')
@shift_required
def order_void(request, order_id):
    """Void an active (unpaid) order. Supervisor/Manager/Superuser only.

    Records the supervisor (authorized_by), reason, and timestamp so the
    voids_per_shift anomaly detector and audit reports can attribute the
    void to both the server (order.waiter) and the approving supervisor.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _can_void(request.user):
        return JsonResponse({'error': 'Not authorised to void orders'}, status=403)

    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'A reason is required to void an order.')
        return redirect('order-detail', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    if order.status != 'active':
        messages.error(request, 'Only unpaid orders can be voided.')
        return redirect('order-detail', order_id=order.id)

    with transaction.atomic():
        for oi in order.items.select_related('menu_item').all():
            try:
                oi.menu_item.restore_stock(oi.quantity)
            except Exception:
                logger.warning("Stock restore failed for %s", oi.menu_item.title)
        order.status = 'cancelled'
        order.authorized_by = request.user
        order.authorization_reason = reason
        order.voided_at = timezone.now()
        order.save()
        if order.table:
            order.table.status = 'available'
            order.table.save()

    messages.success(request, f'Order #{order.id} voided.')
    return redirect('order-detail', order_id=order.id)


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
                        description=f'Order #{order.id} — Table {order.table.number if order.table else "N/A"}',
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
def credit_order_settle(request, order_id):
    """Settle a credit order in full (cash or M-Pesa).

    Servers can mark their own credit orders as paid when the customer
    returns to settle. Creates a credit DebtorTransaction allocated against
    the original invoice and credits the cash account — same accounting
    path as the manager-level receive_payment view.
    """
    if request.method != 'POST':
        return redirect('order-list')

    from django.db.models import Q
    if request.user.is_superuser or _is_supervisor(request.user):
        order = get_object_or_404(Order, id=order_id)
    else:
        order = get_object_or_404(
            Order, Q(waiter=request.user) | Q(created_by=request.user), id=order_id,
        )

    if order.payment_method != 'credit' or not order.debtor_id:
        messages.error(request, 'This order is not on credit.')
        return redirect('order-list')

    from debtor.models import DebtorTransaction, DebtorPaymentAllocation
    try:
        invoice = DebtorTransaction.objects.get(
            transaction_type='debit', reference=str(order.id),
        )
    except DebtorTransaction.DoesNotExist:
        messages.error(request, 'Could not find the invoice for this order.')
        return redirect('order-list')

    if invoice.remaining <= 0:
        messages.info(request, f'Order #{order.id} is already fully paid.')
        return redirect('order-list')

    payment_method = request.POST.get('payment_method', '')
    if payment_method not in ('cash', 'mpesa', 'card'):
        messages.error(request, 'Choose cash, M-Pesa, or card.')
        return redirect('order-list')

    mpesa_code = ''
    card_reference = ''
    if payment_method == 'mpesa':
        mpesa_code = request.POST.get('mpesa_code', '').strip().upper()
        if len(mpesa_code) != 4 or not mpesa_code.isalnum():
            messages.error(request, 'Enter the last 4 characters of the M-Pesa code.')
            return redirect('order-list')
    elif payment_method == 'card':
        card_reference = request.POST.get('card_reference', '').strip()[:50]

    settle_ref = mpesa_code or card_reference
    amount = invoice.remaining
    with transaction.atomic():
        payment_txn = DebtorTransaction.objects.create(
            debtor=order.debtor,
            transaction_type='credit',
            amount=amount,
            description=(
                f'Order #{order.id} settled by {request.user.username} '
                f'({payment_method}{" " + settle_ref if settle_ref else ""})'
            ),
            reference=str(order.id),
            created_by=request.user,
        )
        DebtorPaymentAllocation.objects.create(
            payment=payment_txn, invoice=invoice, amount=amount,
        )
        invoice.amount_paid = invoice.amount
        invoice.save(update_fields=['amount_paid'])

        from administration.models import Account, Transaction as AcctTransaction
        cash_account = Account.get_by_type('cash')
        AcctTransaction.objects.create(
            account=cash_account,
            transaction_type='credit',
            amount=amount,
            description=f'Debtor payment — {order.debtor.name} (Order #{order.id})',
            reference_type='debtor_payment',
            reference_id=payment_txn.id,
            created_by=request.user,
        )

    logging.getLogger('audit').info(
        "Credit order settled: order_id=%d debtor='%s' amount=%s method=%s by=%s",
        order.id, order.debtor.name, amount, payment_method, request.user.username,
    )
    messages.success(request, f'Order #{order.id} marked as paid.')
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
    """End the user's active shift.

    Two-stage for cash-handling staff (servers/cashiers): the shift goes
    into pending_close — is_active flips to False so no new orders can be
    taken, but ended_at stays None. A supervisor finalises ended_at when
    they record the till count on the z-report detail page.

    One-stage for auto-shift users (managers/supervisors/owners/promoters):
    they don't handle cash, so the shift closes immediately.
    """
    if request.method == 'POST':
        shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
        if shift:
            unpaid = shift.orders.filter(status='active').count()
            if unpaid:
                messages.error(
                    request,
                    f'You have {unpaid} unpaid order{"s" if unpaid != 1 else ""}. '
                    'Settle them before clocking out.',
                )
                return redirect('shift')
            if _is_auto_shift_user(request.user):
                shift.ended_at = timezone.now()
                shift.is_active = False
                shift.save()
                return redirect('admin-dashboard')
            # Cash-handling staff: pending close, awaiting supervisor count
            shift.is_active = False
            shift.pending_close_at = timezone.now()
            shift.save()
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
