from functools import wraps

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone

from .models import Category, MenuItem, Table, Order, OrderItem, Shift
from cart.cart import Cart


def shift_required(view_func):
    """Redirect to shift page if waiter has no active shift."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            has_shift = Shift.objects.filter(waiter=request.user, is_active=True).exists()
            if not has_shift:
                return redirect('shift')
        return view_func(request, *args, **kwargs)
    return wrapper


def categories(request):
    all_categories = Category.objects.all()
    return {'all_categories': all_categories}


@login_required(login_url='waiter-login')
@shift_required
def pos_home(request):
    all_products = MenuItem.objects.filter(is_available=True)
    tables = Table.objects.all()
    context = {
        'all_products': all_products,
        'tables': tables,
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

        active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()

        order = Order.objects.create(
            table=table,
            waiter=request.user,
            shift=active_shift,
            notes=order_notes,
            status='active',
        )

        for item in cart:
            OrderItem.objects.create(
                order=order,
                menu_item=item['product'],
                quantity=item['qty'],
            )

        table.status = 'occupied'
        table.save()

        cart.clear()

        return redirect('order-detail', order_id=order.id)

    return redirect('pos')


@login_required(login_url='waiter-login')
@shift_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    return render(request, 'menu/order-detail.html', {'order': order})


@login_required(login_url='waiter-login')
@shift_required
def order_list(request):
    orders = Order.objects.exclude(status__in=['paid', 'cancelled'])
    return render(request, 'menu/order-list.html', {'orders': orders})


@login_required(login_url='waiter-login')
@shift_required
def order_update_status(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        new_status = request.POST.get('status')

        if new_status in dict(Order.STATUS_CHOICES):
            # Handle payment
            if new_status == 'paid':
                payment_method = request.POST.get('payment_method', '')
                if payment_method not in dict(Order.PAYMENT_CHOICES):
                    return redirect('order-detail', order_id=order.id)
                order.payment_method = payment_method
                if payment_method == 'mpesa':
                    mpesa_code = request.POST.get('mpesa_code', '').strip()
                    if not mpesa_code:
                        return redirect('order-detail', order_id=order.id)
                    order.mpesa_code = mpesa_code

            order.status = new_status
            order.save()

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
    return render(request, 'menu/tables.html', {'tables': tables})


# ---- Shift views (no shift_required — this IS the shift page) ----

@login_required(login_url='waiter-login')
def shift_view(request):
    active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
    past_shifts = Shift.objects.filter(waiter=request.user, is_active=False)[:10]

    context = {
        'active_shift': active_shift,
        'past_shifts': past_shifts,
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
            shift.ended_at = timezone.now()
            shift.is_active = False
            shift.save()
    return redirect('shift')


@login_required(login_url='waiter-login')
def shift_detail(request, shift_id):
    shift = get_object_or_404(Shift, id=shift_id, waiter=request.user)
    orders = shift.orders.all()
    context = {
        'shift': shift,
        'orders': orders,
    }
    return render(request, 'menu/shift-detail.html', context)
