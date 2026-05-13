from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.db.models import Sum, Count, Q

from menu.models import (
    Category, MenuItem, InventoryItem, Recipe, Table, Order, OrderItem, Shift,
    RestaurantSettings,
)
from account.models import WaiterCode
from staff_compensation.models import StaffCompensation, PaymentRecord
from .models import Account, Transaction

from .forms import (
    StaffCreateForm, StaffUpdateForm, WaiterCodeForm,
    CategoryForm, MenuItemForm, RecipeForm,
    InventoryItemForm, StockUpdateForm,
    TableForm, RestaurantSettingsForm,
)


def _is_admin_user(user):
    """Return True if user is superuser or in Manager/Supervisor group."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Manager', 'Supervisor']).exists()


def _is_manager(user):
    """Return True if user is superuser or in Manager group (not Supervisor)."""
    if user.is_superuser:
        return True
    return user.groups.filter(name='Manager').exists()


def manager_required(view_func):
    """Allow superusers, Managers, and Supervisors to access admin views."""
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_admin_user(request.user):
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def manager_only(view_func):
    """Restrict to superusers and Manager group only (not Supervisors)."""
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager(request.user):
            messages.error(request, 'You do not have permission to perform this action.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def superuser_only(view_func):
    """Restrict to superusers only."""
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'Only the administrator can perform this action.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


# ═══════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def admin_dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    today_orders = Order.objects.filter(created_at__gte=today_start)
    today_paid = today_orders.filter(status='paid')
    today_sales = sum(o.get_total() for o in today_paid)

    hide_super = not request.user.is_superuser

    active_shifts = Shift.objects.filter(is_active=True).select_related('waiter')
    active_orders_qs = Order.objects.filter(status='active')
    if hide_super:
        active_shifts = active_shifts.exclude(waiter__is_superuser=True)
        active_orders_qs = active_orders_qs.exclude(waiter__is_superuser=True)
    active_orders = active_orders_qs.count()

    low_stock_items = [i for i in InventoryItem.objects.all() if i.is_low_stock]

    staff_count = User.objects.filter(is_superuser=False, is_active=True).count()
    table_stats = {
        'total': Table.objects.count(),
        'available': Table.objects.filter(status='available').count(),
        'occupied': Table.objects.filter(status='occupied').count(),
    }

    # Recent orders
    recent_orders = Order.objects.select_related('waiter', 'table')
    if hide_super:
        recent_orders = recent_orders.exclude(waiter__is_superuser=True)
    recent_orders = recent_orders[:8]

    context = {
        'today_sales': today_sales,
        'today_order_count': today_paid.count(),
        'active_orders': active_orders,
        'active_shifts': active_shifts,
        'low_stock_items': low_stock_items[:5],
        'low_stock_count': len(low_stock_items),
        'staff_count': staff_count,
        'table_stats': table_stats,
        'recent_orders': recent_orders,
        'menu_item_count': MenuItem.objects.count(),
        'category_count': Category.objects.count(),
    }
    return render(request, 'administration/dashboard.html', context)



# ═══════════════════════════════════════════════════════════════════════
#  STAFF MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def staff_list(request):
    staff = User.objects.filter(is_superuser=False).select_related(
        'waiter_code', 'compensation',
    ).prefetch_related('groups').order_by('-date_joined')
    context = {'staff_list': staff}
    return render(request, 'administration/staff_list.html', context)


@superuser_only
def staff_create(request):
    if request.method == 'POST':
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = True
            user.save()
            role = form.cleaned_data['role']
            user.groups.set([role])  # form.save(commit=False) skips group assignment

            if role.name == 'Attendant':
                # Attendants can't login — no login code, but get commission setup
                StaffCompensation.objects.get_or_create(
                    user=user,
                    defaults={
                        'compensation_type': 'commission',
                        'commission_scope': 'both',
                        'commission_rate_regular': 0,
                        'commission_rate_premium': 0,
                    },
                )
            elif role.name == 'Promoter':
                # Promoters can login and earn commission on orders they create
                WaiterCode.objects.create(
                    user=user,
                    code=WaiterCode.generate_code(),
                )
                StaffCompensation.objects.get_or_create(
                    user=user,
                    defaults={
                        'compensation_type': 'commission',
                        'commission_scope': 'both',
                        'commission_rate_regular': 0,
                        'commission_rate_premium': 0,
                    },
                )
            else:
                # Auto-generate login code for loginable staff
                WaiterCode.objects.create(
                    user=user,
                    code=WaiterCode.generate_code(),
                )
            messages.success(request, f'Staff member {user.username} created.')
            return redirect('admin-staff-list')
    else:
        form = StaffCreateForm()
    return render(request, 'administration/staff_form.html', {
        'form': form, 'title': 'Add Staff Member',
    })


@superuser_only
def staff_edit(request, user_id):
    staff_user = get_object_or_404(User, pk=user_id, is_superuser=False)
    waiter_code = getattr(staff_user, 'waiter_code', None)

    if request.method == 'POST':
        form = StaffUpdateForm(request.POST, instance=staff_user)
        wc_form = WaiterCodeForm(request.POST, instance=waiter_code) if waiter_code else None
        if form.is_valid() and (wc_form is None or wc_form.is_valid()):
            form.save()
            if wc_form:
                wc_form.save()
            messages.success(request, f'Staff member {staff_user.username} updated.')
            return redirect('admin-staff-list')
    else:
        form = StaffUpdateForm(instance=staff_user)
        wc_form = WaiterCodeForm(instance=waiter_code) if waiter_code else None

    comp = getattr(staff_user, 'compensation', None)
    return render(request, 'administration/staff_form.html', {
        'form': form,
        'wc_form': wc_form,
        'staff_user': staff_user,
        'compensation': comp,
        'title': f'Edit {staff_user.username}',
    })


@superuser_only
def staff_delete(request, user_id):
    staff_user = get_object_or_404(User, pk=user_id, is_superuser=False)
    if request.method == 'POST':
        name = staff_user.username
        staff_user.delete()
        messages.success(request, f'Staff member {name} deleted.')
        return redirect('admin-staff-list')
    return render(request, 'administration/confirm_delete.html', {
        'object': staff_user,
        'object_name': f'staff member "{staff_user.username}"',
        'cancel_url': 'admin-staff-list',
    })


# ═══════════════════════════════════════════════════════════════════════
#  CATEGORIES
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def category_list(request):
    categories = Category.objects.annotate(item_count=Count('items'))
    return render(request, 'administration/category_list.html', {'categories': categories})


@superuser_only
def category_create(request):
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category created.')
            return redirect('admin-category-list')
    else:
        form = CategoryForm()
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': 'Add Category', 'cancel_url': 'admin-category-list',
    })


@superuser_only
def category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category updated.')
            return redirect('admin-category-list')
    else:
        form = CategoryForm(instance=category)
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': f'Edit {category.name}', 'cancel_url': 'admin-category-list',
    })


@superuser_only
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        category.delete()
        messages.success(request, 'Category deleted.')
        return redirect('admin-category-list')
    return render(request, 'administration/confirm_delete.html', {
        'object': category,
        'object_name': f'category "{category.name}"',
        'cancel_url': 'admin-category-list',
    })


# ═══════════════════════════════════════════════════════════════════════
#  MENU ITEMS
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def menu_item_list(request):
    items = MenuItem.objects.select_related('category', 'inventory_item')
    tier_filter = request.GET.get('tier')
    cat_filter = request.GET.get('category')
    if tier_filter in ('regular', 'premium'):
        items = items.filter(item_tier=tier_filter)
    if cat_filter:
        items = items.filter(category_id=cat_filter)
    categories = Category.objects.all()
    return render(request, 'administration/menu_item_list.html', {
        'items': items,
        'categories': categories,
        'tier_filter': tier_filter,
        'cat_filter': cat_filter,
    })


@superuser_only
def menu_item_create(request):
    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Menu item created.')
            return redirect('admin-menu-list')
    else:
        form = MenuItemForm()
    return render(request, 'administration/menu_item_form.html', {
        'form': form, 'title': 'Add Menu Item',
    })


@superuser_only
def menu_item_edit(request, pk):
    item = get_object_or_404(MenuItem, pk=pk)
    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'{item.title} updated.')
            return redirect('admin-menu-list')
    else:
        form = MenuItemForm(instance=item)
    recipes = item.recipe_items.select_related('inventory_item').all()
    settings = RestaurantSettings.load()
    return render(request, 'administration/menu_item_form.html', {
        'form': form, 'title': f'Edit {item.title}', 'menu_item': item, 'recipes': recipes,
        'current_unit_cost': item.current_unit_cost(),
        'default_markup_percent': settings.default_markup_percent,
        'currency_symbol': settings.currency_symbol,
    })


@superuser_only
def menu_item_delete(request, pk):
    item = get_object_or_404(MenuItem, pk=pk)
    if request.method == 'POST':
        item.delete()
        messages.success(request, 'Menu item deleted.')
        return redirect('admin-menu-list')
    return render(request, 'administration/confirm_delete.html', {
        'object': item,
        'object_name': f'menu item "{item.title}"',
        'cancel_url': 'admin-menu-list',
    })


@superuser_only
def recipe_add(request, menu_item_id):
    item = get_object_or_404(MenuItem, pk=menu_item_id)
    if request.method == 'POST':
        form = RecipeForm(request.POST)
        if form.is_valid():
            recipe = form.save(commit=False)
            recipe.menu_item = item
            recipe.save()
            messages.success(request, 'Ingredient added.')
            return redirect('admin-menu-edit', pk=item.pk)
    else:
        form = RecipeForm()
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': f'Add Ingredient to {item.title}',
        'cancel_url': 'admin-menu-list',
    })


@superuser_only
def recipe_delete(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    menu_item_id = recipe.menu_item_id
    if request.method == 'POST':
        recipe.delete()
        messages.success(request, 'Ingredient removed.')
        return redirect('admin-menu-edit', pk=menu_item_id)
    return render(request, 'administration/confirm_delete.html', {
        'object': recipe,
        'object_name': f'ingredient "{recipe.inventory_item.name}" from {recipe.menu_item.title}',
        'cancel_url': 'admin-menu-list',
    })


# ═══════════════════════════════════════════════════════════════════════
#  INVENTORY
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def inventory_list(request):
    items = InventoryItem.objects.all()
    show = request.GET.get('show')
    if show == 'low':
        items = [i for i in items if i.is_low_stock]
    else:
        items = list(items)
    return render(request, 'administration/inventory_list.html', {
        'items': items, 'show': show,
    })


@superuser_only
def inventory_create(request):
    if request.method == 'POST':
        form = InventoryItemForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Inventory item created.')
            return redirect('admin-inventory-list')
    else:
        form = InventoryItemForm()
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': 'Add Inventory Item', 'cancel_url': 'admin-inventory-list',
    })


@superuser_only
def inventory_edit(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == 'POST':
        form = InventoryItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'{item.name} updated.')
            return redirect('admin-inventory-list')
    else:
        form = InventoryItemForm(instance=item)
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': f'Edit {item.name}', 'cancel_url': 'admin-inventory-list',
    })


@superuser_only
def inventory_delete(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == 'POST':
        item.delete()
        messages.success(request, 'Inventory item deleted.')
        return redirect('admin-inventory-list')
    return render(request, 'administration/confirm_delete.html', {
        'object': item,
        'object_name': f'inventory item "{item.name}"',
        'cancel_url': 'admin-inventory-list',
    })


# ═══════════════════════════════════════════════════════════════════════
#  TABLES
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def table_list(request):
    tables = Table.objects.all()
    return render(request, 'administration/table_list.html', {'tables': tables})


@superuser_only
def table_create(request):
    if request.method == 'POST':
        form = TableForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Table created.')
            return redirect('admin-table-list')
    else:
        form = TableForm()
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': 'Add Table', 'cancel_url': 'admin-table-list',
    })


@superuser_only
def table_edit(request, pk):
    table = get_object_or_404(Table, pk=pk)
    if request.method == 'POST':
        form = TableForm(request.POST, instance=table)
        if form.is_valid():
            form.save()
            messages.success(request, f'Table {table.number} updated.')
            return redirect('admin-table-list')
    else:
        form = TableForm(instance=table)
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': f'Edit Table {table.number}', 'cancel_url': 'admin-table-list',
    })


@superuser_only
def table_delete(request, pk):
    table = get_object_or_404(Table, pk=pk)
    if request.method == 'POST':
        table.delete()
        messages.success(request, 'Table deleted.')
        return redirect('admin-table-list')
    return render(request, 'administration/confirm_delete.html', {
        'object': table,
        'object_name': f'Table {table.number}',
        'cancel_url': 'admin-table-list',
    })


# ═══════════════════════════════════════════════════════════════════════
#  ORDERS
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def order_list_admin(request):
    base_qs = Order.objects.select_related('waiter', 'table').all()
    if not request.user.is_superuser:
        base_qs = base_qs.exclude(waiter__is_superuser=True)
    status_filter = request.GET.get('status', 'active')
    if status_filter in ('active', 'paid', 'cancelled'):
        orders = base_qs.filter(status=status_filter)
    else:
        status_filter = 'all'
        orders = base_qs

    unpaid_orders = base_qs.filter(status='active')
    total_unpaid = sum(o.get_total() for o in unpaid_orders)

    return render(request, 'administration/order_list.html', {
        'orders': orders[:50],
        'status_filter': status_filter,
        'unpaid_count': unpaid_orders.count(),
        'total_unpaid': total_unpaid,
    })


# ═══════════════════════════════════════════════════════════════════════
#  SHIFTS
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def shift_list_admin(request):
    shifts = Shift.objects.select_related('waiter').all()
    if not request.user.is_superuser:
        shifts = shifts.exclude(waiter__is_superuser=True)
    show = request.GET.get('show', 'active')
    if show == 'active':
        shifts = shifts.filter(is_active=True)
    return render(request, 'administration/shift_list.html', {
        'shifts': shifts[:50], 'show': show,
    })


# ═══════════════════════════════════════════════════════════════════════
#  RESTAURANT SETTINGS
# ═══════════════════════════════════════════════════════════════════════

@superuser_only
def settings_view(request):
    settings_obj = RestaurantSettings.load()
    if request.method == 'POST':
        form = RestaurantSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings updated.')
            return redirect('admin-settings')
    else:
        form = RestaurantSettingsForm(instance=settings_obj)
    return render(request, 'administration/settings.html', {'form': form})


# ═══════════════════════════════════════════════════════════════════════
#  REPORTS
# ═══════════════════════════════════════════════════════════════════════

@manager_only
def reports_view(request):
    from datetime import timedelta
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def sales_for_range(start, end):
        orders = Order.objects.filter(status='paid', created_at__gte=start, created_at__lte=end)
        return {
            'count': orders.count(),
            'total': sum(o.get_total() for o in orders),
        }

    # Staff performance this month
    staff_perf = []
    for user in User.objects.filter(is_superuser=False, is_active=True):
        orders = Order.objects.filter(waiter=user, status='paid', created_at__gte=month_start)
        total = sum(o.get_total() for o in orders)
        staff_perf.append({
            'user': user,
            'order_count': orders.count(),
            'total_sales': total,
        })
    staff_perf.sort(key=lambda x: x['total_sales'], reverse=True)

    # Payment method breakdown this month
    paid_orders = Order.objects.filter(status='paid', created_at__gte=month_start)
    payment_methods = {}
    for order in paid_orders:
        method = order.get_payment_method_display() or 'Unknown'
        if method not in payment_methods:
            payment_methods[method] = {'count': 0, 'total': 0}
        payment_methods[method]['count'] += 1
        payment_methods[method]['total'] += order.get_total()

    # Top selling items this month
    top_items = (
        OrderItem.objects
        .filter(order__status='paid', order__created_at__gte=month_start)
        .values('menu_item__title', 'menu_item__item_tier')
        .annotate(qty=Sum('quantity'))
        .order_by('-qty')[:10]
    )

    context = {
        'today': sales_for_range(today_start, now),
        'this_week': sales_for_range(week_start, now),
        'this_month': sales_for_range(month_start, now),
        'staff_performance': staff_perf[:10],
        'payment_methods': payment_methods,
        'top_items': top_items,
    }
    return render(request, 'administration/reports.html', context)


# ═══════════════════════════════════════════════════════════════════════
#  ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════

@manager_only
def accounts_overview(request):
    """Show all accounts with balances and recent transactions."""
    from datetime import timedelta

    # Ensure default accounts exist
    for acct_type, _ in Account.ACCOUNT_TYPE_CHOICES:
        Account.get_by_type(acct_type)

    accounts = Account.objects.all()
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    cash_data = []
    receivable_data = []
    cash_total = 0
    receivable_total = 0
    for acct in accounts:
        bal = acct.balance
        is_receivable = acct.account_type.endswith(Account.RECEIVABLE_SUFFIX)
        if is_receivable:
            receivable_total += bal
        else:
            cash_total += bal
        today_credits = acct.transactions.filter(
            transaction_type='credit', created_at__gte=today_start,
        ).aggregate(t=Sum('amount'))['t'] or 0
        today_debits = acct.transactions.filter(
            transaction_type='debit', created_at__gte=today_start,
        ).aggregate(t=Sum('amount'))['t'] or 0
        entry = {
            'account': acct,
            'balance': bal,
            'today_in': today_credits,
            'today_out': today_debits,
            'is_receivable': is_receivable,
        }
        (receivable_data if is_receivable else cash_data).append(entry)

    # Recent transactions across all accounts
    recent_txns = Transaction.objects.select_related(
        'account', 'created_by',
    )[:20]

    context = {
        'cash_data': cash_data,
        'receivable_data': receivable_data,
        'cash_total': cash_total,
        'receivable_total': receivable_total,
        'total_balance': cash_total + receivable_total,
        'recent_transactions': recent_txns,
        'current_month': now.strftime('%B %Y'),
    }
    return render(request, 'administration/accounts.html', context)


@manager_only
def account_detail(request, pk):
    """Account statement with date filtering and running balance."""
    from datetime import datetime, timedelta
    from decimal import Decimal

    account = get_object_or_404(Account, pk=pk)

    # Date range filtering
    date_from = request.GET.get('from')
    date_to = request.GET.get('to')

    now = timezone.now()
    if date_from:
        try:
            date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
        except ValueError:
            date_from = None
            date_from_parsed = None
    else:
        date_from_parsed = None

    if date_to:
        try:
            date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError:
            date_to = None
            date_to_parsed = None
    else:
        date_to_parsed = None

    transactions = account.transactions.select_related('created_by').all()

    txn_type = request.GET.get('type')
    if txn_type in ('credit', 'debit'):
        transactions = transactions.filter(transaction_type=txn_type)

    if date_from_parsed:
        transactions = transactions.filter(created_at__date__gte=date_from_parsed)
    if date_to_parsed:
        transactions = transactions.filter(created_at__date__lte=date_to_parsed)

    transactions = list(transactions[:200])

    # Compute totals for the filtered period
    total_credits = sum(t.amount for t in transactions if t.transaction_type == 'credit')
    total_debits = sum(t.amount for t in transactions if t.transaction_type == 'debit')
    net = total_credits - total_debits

    # Opening balance: balance of all transactions BEFORE the date range
    opening_balance = Decimal('0')
    if date_from_parsed:
        before_qs = account.transactions.filter(created_at__date__lt=date_from_parsed)
        agg = before_qs.aggregate(
            credits=Sum('amount', filter=Q(transaction_type='credit')),
            debits=Sum('amount', filter=Q(transaction_type='debit')),
        )
        opening_balance = (agg['credits'] or Decimal('0')) - (agg['debits'] or Decimal('0'))

    # Running balance (oldest first for calculation, but display newest first)
    closing_balance = opening_balance + net
    reversed_txns = list(reversed(transactions))
    running = opening_balance
    for txn in reversed_txns:
        if txn.transaction_type == 'credit':
            running += txn.amount
        else:
            running -= txn.amount
        txn.running_balance = running

    context = {
        'account': account,
        'transactions': transactions,
        'balance': account.balance,
        'txn_type': txn_type,
        'date_from': date_from or '',
        'date_to': date_to or '',
        'total_credits': total_credits,
        'total_debits': total_debits,
        'net': net,
        'opening_balance': opening_balance,
        'closing_balance': closing_balance,
        'has_date_filter': bool(date_from_parsed or date_to_parsed),
    }
    return render(request, 'administration/account_detail.html', context)


# ═══════════════════════════════════════════════════════════════════════
#  ACCOUNT TRANSFERS
# ═══════════════════════════════════════════════════════════════════════

@superuser_only
def transfer_funds(request):
    """Transfer money between accounts."""
    from decimal import Decimal
    from django.db import transaction as db_transaction

    # Ensure default accounts exist
    for acct_type, _ in Account.ACCOUNT_TYPE_CHOICES:
        Account.get_by_type(acct_type)

    accounts = Account.objects.filter(is_active=True)

    if request.method == 'POST':
        from_id = request.POST.get('from_account')
        to_id = request.POST.get('to_account')
        amount = request.POST.get('amount', '0')
        note = request.POST.get('note', '').strip()

        try:
            amount = Decimal(amount).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('admin-transfer-funds')

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('admin-transfer-funds')

        if from_id == to_id:
            messages.error(request, 'Source and destination accounts must be different.')
            return redirect('admin-transfer-funds')

        from_account = get_object_or_404(Account, pk=from_id)
        to_account = get_object_or_404(Account, pk=to_id)

        description = note or f'Transfer: {from_account.name} → {to_account.name}'

        with db_transaction.atomic():
            # Debit source account
            debit_txn = Transaction.objects.create(
                account=from_account,
                transaction_type='debit',
                amount=amount,
                description=description,
                reference_type='transfer',
                created_by=request.user,
            )
            # Credit destination account
            credit_txn = Transaction.objects.create(
                account=to_account,
                transaction_type='credit',
                amount=amount,
                description=description,
                reference_type='transfer',
                reference_id=debit_txn.id,
                created_by=request.user,
            )
            # Link debit back to credit
            debit_txn.reference_id = credit_txn.id
            debit_txn.save(update_fields=['reference_id'])

        restaurant = RestaurantSettings.load()
        symbol = restaurant.currency_symbol
        messages.success(
            request,
            f'Transferred {symbol} {amount:,.2f} from {from_account.name} to {to_account.name}.',
        )
        return redirect('admin-accounts')

    account_data = []
    for acct in accounts:
        account_data.append({
            'account': acct,
            'balance': acct.balance,
        })

    return render(request, 'administration/transfer_funds.html', {
        'accounts': accounts,
        'account_data': account_data,
    })
