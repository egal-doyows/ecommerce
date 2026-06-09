from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as tz

from menu.cache import get_restaurant_settings
from menu.models import InventoryItem, MenuItem, _InsufficientStock

from .models import StaffMealLog, StaffMealItem


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _is_admin_user(user):
    """Manager, Supervisor, or Superuser."""
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )


def staff_required(view_func):
    """Managers and Supervisors can log and view staff meals."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_admin_user(request.user):
            messages.error(request, 'You do not have permission to access staff meals.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@staff_required
def staff_meal_list(request):
    qs = StaffMealLog.objects.select_related('logged_by').prefetch_related(
        'items__menu_item', 'items__inventory_item',
    )

    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    meal_filter = request.GET.get('meal_type', '')
    search = request.GET.get('q', '')

    if date_from:
        try:
            qs = qs.filter(date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            qs = qs.filter(date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    if meal_filter:
        qs = qs.filter(meal_type=meal_filter)
    if search:
        qs = qs.filter(
            models.Q(notes__icontains=search)
            | models.Q(items__menu_item__title__icontains=search)
            | models.Q(items__inventory_item__name__icontains=search)
        ).distinct()

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    restaurant = get_restaurant_settings()

    return render(request, 'staff_meals/staff_meal_list.html', {
        'logs': page_obj,
        'page_obj': page_obj,
        'date_from': date_from,
        'date_to': date_to,
        'meal_filter': meal_filter,
        'search': search,
        'meal_choices': StaffMealLog.MEAL_TYPE_CHOICES,
        'restaurant': restaurant,
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def _parse_lines(request):
    """Collect staff-meal line items from the POST.

    Each row posts `line_{idx}` = "menu:<pk>" or "inv:<pk>",
    plus `line_qty_{idx}` and `line_notes_{idx}`.
    Returns a list of dicts ready for StaffMealItem creation, with the
    resolved object and a snapshot unit_cost.
    """
    lines = []
    for key in request.POST:
        if not key.startswith('line_') or key.startswith('line_qty_') or key.startswith('line_notes_'):
            continue
        idx = key[len('line_'):]
        raw = request.POST.get(key, '')
        if ':' not in raw:
            continue
        kind, _, pk = raw.partition(':')

        try:
            qty = Decimal(request.POST.get(f'line_qty_{idx}', '0'))
        except Exception:
            qty = Decimal('0')
        if qty <= 0:
            continue

        notes = request.POST.get(f'line_notes_{idx}', '')

        if kind == 'menu':
            mi = MenuItem.objects.filter(pk=pk).first()
            if not mi:
                continue
            lines.append({
                'menu_item': mi,
                'inventory_item': None,
                'quantity': qty,
                'unit_cost': mi.current_unit_cost(),
                'notes': notes,
            })
        elif kind == 'inv':
            inv = InventoryItem.objects.filter(pk=pk).first()
            if not inv:
                continue
            lines.append({
                'menu_item': None,
                'inventory_item': inv,
                'quantity': qty,
                'unit_cost': inv.buying_price,
                'notes': notes,
            })
    return lines


@staff_required
def staff_meal_create(request):
    restaurant = get_restaurant_settings()
    menu_items = MenuItem.objects.filter(is_available=True).order_by('title')
    inventory_items = InventoryItem.objects.order_by('name')

    if request.method == 'POST':
        meal_type = request.POST.get('meal_type', '')
        date_str = request.POST.get('date', '')
        notes = request.POST.get('notes', '')

        if not meal_type:
            messages.error(request, 'Please select a meal type.')
            return redirect('staff-meal-create')

        try:
            meal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            meal_date = tz.now().date()

        lines = _parse_lines(request)
        if not lines:
            messages.error(request, 'Please add at least one item with a valid quantity.')
            return redirect('staff-meal-create')

        try:
            with transaction.atomic():
                log = StaffMealLog.objects.create(
                    meal_type=meal_type,
                    date=meal_date,
                    notes=notes,
                    logged_by=request.user,
                )
                for ln in lines:
                    StaffMealItem.objects.create(
                        staff_meal_log=log,
                        menu_item=ln['menu_item'],
                        inventory_item=ln['inventory_item'],
                        quantity=ln['quantity'],
                        unit_cost=ln['unit_cost'],
                        notes=ln['notes'],
                    )
                    # Deduct stock — raises _InsufficientStock to roll back.
                    if ln['menu_item'] is not None:
                        ln['menu_item'].deduct_stock(ln['quantity'])
                    elif not ln['inventory_item'].deduct(ln['quantity']):
                        raise _InsufficientStock(ln['inventory_item'].name)
        except _InsufficientStock as exc:
            messages.error(
                request,
                f'Not enough stock for "{exc}". Nothing was recorded — '
                f'reduce the quantity or restock first.',
            )
            return redirect('staff-meal-create')

        messages.success(request, f'Staff meal {log.meal_number} recorded successfully.')
        return redirect('staff-meal-detail', pk=log.pk)

    return render(request, 'staff_meals/staff_meal_create.html', {
        'menu_items': menu_items,
        'inventory_items': inventory_items,
        'meal_choices': StaffMealLog.MEAL_TYPE_CHOICES,
        'today': tz.now().date().isoformat(),
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@staff_required
def staff_meal_detail(request, pk):
    log = get_object_or_404(
        StaffMealLog.objects.select_related('logged_by'),
        pk=pk,
    )
    items = log.items.select_related('menu_item', 'inventory_item').all()
    restaurant = get_restaurant_settings()

    return render(request, 'staff_meals/staff_meal_detail.html', {
        'log': log,
        'items': items,
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


# ---------------------------------------------------------------------------
# Summary / Analytics
# ---------------------------------------------------------------------------

@staff_required
def staff_meal_summary(request):
    restaurant = get_restaurant_settings()

    today = tz.now().date()
    date_from = request.GET.get('date_from', today.replace(day=1).isoformat())
    date_to = request.GET.get('date_to', today.isoformat())

    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d').date()
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        d_to = today

    logs = StaffMealLog.objects.filter(date__gte=d_from, date__lte=d_to)
    meal_items = StaffMealItem.objects.filter(
        staff_meal_log__date__gte=d_from,
        staff_meal_log__date__lte=d_to,
    ).select_related('menu_item', 'inventory_item', 'staff_meal_log')

    total_cost = sum(mi.cost for mi in meal_items)
    total_events = logs.count()
    total_items = meal_items.count()

    # By meal type
    by_meal = []
    for code, label in StaffMealLog.MEAL_TYPE_CHOICES:
        type_items = [mi for mi in meal_items if mi.staff_meal_log.meal_type == code]
        cost = sum(mi.cost for mi in type_items)
        if type_items:
            by_meal.append({
                'meal': label,
                'code': code,
                'count': len(type_items),
                'cost': cost,
                'pct': round(cost / total_cost * 100, 1) if total_cost else 0,
            })
    by_meal.sort(key=lambda x: x['cost'], reverse=True)

    # Top consumed items (menu + inventory combined)
    item_totals = {}
    for mi in meal_items:
        key = f'menu:{mi.menu_item_id}' if mi.menu_item_id else f'inv:{mi.inventory_item_id}'
        if key not in item_totals:
            item_totals[key] = {
                'name': mi.item_name,
                'unit': mi.unit_label,
                'total_qty': Decimal('0'),
                'total_cost': Decimal('0'),
                'count': 0,
            }
        item_totals[key]['total_qty'] += mi.quantity
        item_totals[key]['total_cost'] += mi.cost
        item_totals[key]['count'] += 1

    top_items = sorted(item_totals.values(), key=lambda x: x['total_cost'], reverse=True)[:10]

    return render(request, 'staff_meals/staff_meal_summary.html', {
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
        'date_from': date_from,
        'date_to': date_to,
        'total_cost': total_cost,
        'total_events': total_events,
        'total_items': total_items,
        'by_meal': by_meal,
        'top_items': top_items,
    })
