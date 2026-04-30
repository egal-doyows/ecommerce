from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.db.models import Sum, Count, Q, F

from core.permissions import (
    is_admin_user, is_manager, is_overall_manager, has_full_access,
    admin_required as manager_required,
    manager_required as manager_only,
    overall_manager_required,
    full_access_required as superuser_only,
)
from menu.models import (
    Category, MenuItem, InventoryItem, Recipe, Table, Order, OrderItem, Shift,
    RestaurantSettings, BranchMenuAvailability,
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


# ═══════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def admin_dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    today_orders = Order.objects.filter(created_at__gte=today_start, branch=request.branch)
    today_paid = today_orders.filter(status='paid')
    today_sales = today_paid.annotate(
        order_total=Sum(F('items__unit_price') * F('items__quantity'))
    ).aggregate(total=Sum('order_total'))['total'] or 0

    hide_super = not request.user.is_superuser

    active_shifts = Shift.objects.filter(is_active=True, branch=request.branch).select_related('waiter')
    active_orders_qs = Order.objects.filter(status='active', branch=request.branch)
    if hide_super:
        active_shifts = active_shifts.exclude(waiter__is_superuser=True)
        active_orders_qs = active_orders_qs.exclude(waiter__is_superuser=True)
    active_orders = active_orders_qs.count()

    low_stock_items = list(InventoryItem.objects.filter(
        branch=request.branch, stock_quantity__lte=F('low_stock_threshold'),
    ))

    staff_count = User.objects.filter(is_superuser=False, is_active=True).count()
    table_stats = {
        'total': Table.objects.filter(branch=request.branch).count(),
        'available': Table.objects.filter(branch=request.branch, status='available').count(),
        'occupied': Table.objects.filter(branch=request.branch, status='occupied').count(),
    }

    # Recent orders
    recent_orders = Order.objects.filter(branch=request.branch).select_related('waiter', 'table')
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
    from branches.models import UserBranch
    qs = User.objects.filter(is_superuser=False)
    if request.branch:
        branch_user_ids = UserBranch.objects.filter(
            branch=request.branch,
        ).values_list('user_id', flat=True)
        qs = qs.filter(pk__in=branch_user_ids)
    staff = qs.select_related(
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
            elif role.name == 'Marketing':
                # Marketing can login and earns commission on orders they create
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

            # Auto-assign to current branch
            from branches.models import UserBranch
            from branches.utils import resolve_branch
            target_branch = resolve_branch(request)
            if target_branch:
                UserBranch.objects.get_or_create(
                    user=user, branch=target_branch,
                    defaults={'is_primary': True},
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
        # Soft-delete: deactivate instead of destroying historical data
        staff_user.is_active = False
        staff_user.save()
        # Deactivate waiter code if it exists
        if hasattr(staff_user, 'waiter_code'):
            staff_user.waiter_code.is_active = False
            staff_user.waiter_code.save()
        messages.success(request, f'Staff member {name} deactivated.')
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

    # Per-branch availability for current branch
    branch = request.branch
    overrides = {}
    if branch:
        for ba in BranchMenuAvailability.objects.filter(branch=branch):
            overrides[ba.menu_item_id] = ba.is_available

    items_with_availability = []
    for item in items:
        if item.pk in overrides:
            branch_available = overrides[item.pk]
        else:
            branch_available = item.is_available
        item.branch_available = branch_available
        items_with_availability.append(item)

    return render(request, 'administration/menu_item_list.html', {
        'items': items_with_availability,
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
    return render(request, 'administration/menu_item_form.html', {
        'form': form, 'title': f'Edit {item.title}', 'menu_item': item, 'recipes': recipes,
    })


@superuser_only
def menu_item_delete(request, pk):
    item = get_object_or_404(MenuItem, pk=pk)
    if request.method == 'POST':
        # Soft-delete: mark unavailable instead of destroying historical order data
        item.is_available = False
        item.save()
        messages.success(request, f'Menu item "{item.title}" deactivated.')
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


# ── Branch Menu Availability ────────────────────────────────────────

@manager_required
def branch_menu_availability(request):
    """Manage which menu items are available at the current branch."""
    from branches.models import Branch

    branch = request.branch
    all_branches = Branch.objects.filter(is_active=True) if is_overall_manager(request.user) else None

    # Allow overall managers to view/edit another branch
    view_branch_id = request.GET.get('branch')
    if view_branch_id and is_overall_manager(request.user):
        try:
            branch = Branch.objects.get(pk=view_branch_id, is_active=True)
        except Branch.DoesNotExist:
            pass

    items = MenuItem.objects.select_related('category').order_by('category__name', 'title')
    overrides = {
        ba.menu_item_id: ba.is_available
        for ba in BranchMenuAvailability.objects.filter(branch=branch)
    }

    items_data = []
    for item in items:
        has_override = item.pk in overrides
        branch_available = overrides[item.pk] if has_override else item.is_available
        items_data.append({
            'item': item,
            'branch_available': branch_available,
            'has_override': has_override,
        })

    categories = Category.objects.all()
    cat_filter = request.GET.get('category')

    if cat_filter:
        items_data = [d for d in items_data if str(d['item'].category_id) == cat_filter]

    return render(request, 'administration/branch_menu_availability.html', {
        'items_data': items_data,
        'categories': categories,
        'cat_filter': cat_filter,
        'target_branch': branch,
        'all_branches': all_branches,
    })


@manager_required
def toggle_branch_availability(request, menu_item_id):
    """Toggle a menu item's availability for the current branch."""
    from branches.models import Branch

    if request.method != 'POST':
        return redirect('admin-branch-menu')

    item = get_object_or_404(MenuItem, pk=menu_item_id)

    branch = request.branch
    branch_id = request.POST.get('target_branch')
    if branch_id and is_overall_manager(request.user):
        try:
            branch = Branch.objects.get(pk=branch_id, is_active=True)
        except Branch.DoesNotExist:
            pass

    ba, created = BranchMenuAvailability.objects.get_or_create(
        branch=branch,
        menu_item=item,
        defaults={'is_available': not item.is_available},
    )
    if not created:
        ba.is_available = not ba.is_available
        ba.save()

    from django.utils.http import url_has_allowed_host_and_scheme
    redirect_url = request.META.get('HTTP_REFERER', '')
    if redirect_url and url_has_allowed_host_and_scheme(redirect_url, allowed_hosts={request.get_host()}):
        return redirect(redirect_url)
    return redirect('admin-branch-menu')


# ═══════════════════════════════════════════════════════════════════════
#  INVENTORY
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def inventory_list(request):
    items = InventoryItem.objects.filter(branch=request.branch)
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
    from branches.utils import resolve_branch
    if request.method == 'POST':
        form = InventoryItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.branch = resolve_branch(request)
            item.save()
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
        # Check if this item is used in any recipes or linked to menu items
        has_recipes = item.used_in_recipes.exists()
        has_menu_items = item.menu_items.exists()
        if has_recipes or has_menu_items:
            # Soft-delete: zero out stock instead of destroying relationships
            item.stock_quantity = 0
            item.save()
            messages.warning(request, f'"{item.name}" stock zeroed out (still referenced by menu items/recipes).')
        else:
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
    tables = Table.objects.filter(branch=request.branch)
    return render(request, 'administration/table_list.html', {'tables': tables})


@superuser_only
def table_create(request):
    from branches.utils import resolve_branch
    if request.method == 'POST':
        form = TableForm(request.POST)
        if form.is_valid():
            table = form.save(commit=False)
            table.branch = resolve_branch(request)
            table.save()
            messages.success(request, 'Space created.')
            return redirect('admin-table-list')
    else:
        form = TableForm()
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': 'Add Space', 'cancel_url': 'admin-table-list',
    })


@superuser_only
def table_edit(request, pk):
    table = get_object_or_404(Table, pk=pk)
    if request.method == 'POST':
        form = TableForm(request.POST, instance=table)
        if form.is_valid():
            form.save()
            messages.success(request, f'Space {table.number} updated.')
            return redirect('admin-table-list')
    else:
        form = TableForm(instance=table)
    return render(request, 'administration/generic_form.html', {
        'form': form, 'title': f'Edit Space {table.number}', 'cancel_url': 'admin-table-list',
    })


@superuser_only
def table_delete(request, pk):
    table = get_object_or_404(Table, pk=pk)
    if request.method == 'POST':
        table.delete()
        messages.success(request, 'Space deleted.')
        return redirect('admin-table-list')
    return render(request, 'administration/confirm_delete.html', {
        'object': table,
        'object_name': f'Space {table.number}',
        'cancel_url': 'admin-table-list',
    })


# ═══════════════════════════════════════════════════════════════════════
#  ORDERS
# ═══════════════════════════════════════════════════════════════════════

@manager_required
def order_list_admin(request):
    base_qs = Order.objects.filter(branch=request.branch).select_related('waiter', 'table')
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
    shifts = Shift.objects.filter(branch=request.branch).select_related('waiter')
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
    from decimal import Decimal
    from branches.models import Branch

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Always scope to the current branch
    scope_branch = request.branch
    view_all = False

    def scoped_orders(**extra):
        qs = Order.objects.filter(status='paid', **extra)
        if scope_branch:
            qs = qs.filter(branch=scope_branch)
        return qs

    def sales_for_range(start, end):
        orders = scoped_orders(created_at__gte=start, created_at__lte=end)
        return {
            'count': orders.count(),
            'total': sum(o.get_total() for o in orders),
        }

    # Staff performance this month
    staff_perf = []
    staff_qs = User.objects.filter(is_superuser=False, is_active=True)
    if scope_branch:
        from branches.models import UserBranch
        branch_ids = UserBranch.objects.filter(branch=scope_branch).values_list('user_id', flat=True)
        staff_qs = staff_qs.filter(pk__in=branch_ids)
    for user in staff_qs:
        orders = scoped_orders(waiter=user, created_at__gte=month_start)
        total = sum(o.get_total() for o in orders)
        if total > 0:
            staff_perf.append({
                'user': user,
                'order_count': orders.count(),
                'total_sales': total,
            })
    staff_perf.sort(key=lambda x: x['total_sales'], reverse=True)

    # Payment method breakdown this month
    paid_orders = scoped_orders(created_at__gte=month_start)
    payment_methods = {}
    for order in paid_orders:
        method = order.get_payment_method_display() or 'Unknown'
        if method not in payment_methods:
            payment_methods[method] = {'count': 0, 'total': Decimal('0')}
        payment_methods[method]['count'] += 1
        payment_methods[method]['total'] += order.get_total()

    # Top selling items this month (grouped by tier)
    top_items_qs = OrderItem.objects.filter(
        order__status='paid', order__created_at__gte=month_start,
    )
    if scope_branch:
        top_items_qs = top_items_qs.filter(order__branch=scope_branch)
    top_items_flat = (
        top_items_qs
        .values('menu_item__title', 'menu_item__item_tier')
        .annotate(
            qty=Sum('quantity'),
            total=Sum(F('unit_price') * F('quantity')),
        )
        .order_by('-qty')[:20]
    )
    from collections import OrderedDict
    top_items = OrderedDict()
    for item in top_items_flat:
        tier = item['menu_item__item_tier'] or 'regular'
        tier_label = 'Premium' if tier == 'premium' else 'Regular'
        top_items.setdefault(tier_label, []).append(item)

    # Sales by category this month
    cat_sales_qs = OrderItem.objects.filter(
        order__status='paid', order__created_at__gte=month_start,
    )
    if scope_branch:
        cat_sales_qs = cat_sales_qs.filter(order__branch=scope_branch)
    category_sales = (
        cat_sales_qs
        .values('menu_item__category__name')
        .annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum(F('unit_price') * F('quantity')),
        )
        .order_by('-total_revenue')
    )

    # Daily sales trend (last 7 days)
    daily_sales = []
    for i in range(6, -1, -1):
        day_start = (today_start - timedelta(days=i))
        day_end = day_start + timedelta(days=1)
        orders = scoped_orders(created_at__gte=day_start, created_at__lt=day_end)
        total = sum(o.get_total() for o in orders)
        daily_sales.append({
            'date': day_start,
            'label': day_start.strftime('%a %d/%m'),
            'total': float(total),
            'count': orders.count(),
        })
    max_daily = max((d['total'] for d in daily_sales), default=1) or 1

    # Hourly sales distribution (today)
    hourly_sales = []
    for h in range(24):
        hour_start = today_start.replace(hour=h)
        hour_end = today_start.replace(hour=h, minute=59, second=59)
        orders = scoped_orders(created_at__gte=hour_start, created_at__lte=hour_end)
        total = sum(o.get_total() for o in orders)
        hourly_sales.append({
            'hour': h,
            'total': total,
            'count': orders.count(),
        })
    max_hourly = max((h['total'] for h in hourly_sales), default=1) or 1

    # Per-branch breakdown for overall managers
    branch_sales = []
    if view_all and branches:
        for branch in branches:
            b_today = Order.objects.filter(
                branch=branch, status='paid', created_at__gte=today_start,
            )
            b_month = Order.objects.filter(
                branch=branch, status='paid', created_at__gte=month_start,
            )
            branch_sales.append({
                'branch': branch,
                'today_sales': sum(o.get_total() for o in b_today),
                'today_count': b_today.count(),
                'month_sales': sum(o.get_total() for o in b_month),
                'month_count': b_month.count(),
            })
        branch_sales.sort(key=lambda x: x['month_sales'], reverse=True)

    today_data = sales_for_range(today_start, now)
    month_data = sales_for_range(month_start, now)

    context = {
        'is_overall': False,
        'branches': None,
        'selected_branch': scope_branch,
        'view_all': False,
        'scope_label': scope_branch.display_name if scope_branch else 'Reports',
        'today': today_data,
        'this_week': sales_for_range(week_start, now),
        'this_month': month_data,
        'staff_performance': staff_perf[:10],
        'payment_methods': payment_methods,
        'top_items': top_items,
        'category_sales': category_sales,
        'daily_sales': daily_sales,
        'max_daily': max_daily,
        'hourly_sales': hourly_sales,
        'max_hourly': max_hourly,
        'branch_sales': branch_sales,
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
        Account.get_by_type(acct_type, branch=request.branch)

    accounts = Account.objects.filter(branch=request.branch)
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    account_data = []
    total_balance = 0
    for acct in accounts:
        bal = acct.balance
        total_balance += bal
        today_credits = acct.transactions.filter(
            transaction_type='credit', created_at__gte=today_start,
        ).aggregate(t=Sum('amount'))['t'] or 0
        today_debits = acct.transactions.filter(
            transaction_type='debit', created_at__gte=today_start,
        ).aggregate(t=Sum('amount'))['t'] or 0
        account_data.append({
            'account': acct,
            'balance': bal,
            'today_in': today_credits,
            'today_out': today_debits,
        })

    # Recent transactions across all accounts
    recent_txns = Transaction.objects.filter(branch=request.branch).select_related(
        'account', 'created_by',
    )[:20]

    context = {
        'account_data': account_data,
        'total_balance': total_balance,
        'recent_transactions': recent_txns,
        'current_month': now.strftime('%B %Y'),
    }
    return render(request, 'administration/accounts.html', context)


@manager_only
def account_detail(request, pk):
    """Account statement with date filtering and running balance."""
    from datetime import datetime, timedelta
    from decimal import Decimal

    account = get_object_or_404(Account, pk=pk, branch=request.branch)

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
        Account.get_by_type(acct_type, branch=request.branch)

    accounts = Account.objects.filter(is_active=True, branch=request.branch)

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
                branch=request.branch,
                transaction_type='debit',
                amount=amount,
                description=description,
                reference_type='transfer',
                created_by=request.user,
            )
            # Credit destination account
            credit_txn = Transaction.objects.create(
                account=to_account,
                branch=request.branch,
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


# ═══════════════════════════════════════════════════════════════════════
#  OVERALL MANAGER — CROSS-BRANCH ANALYTICS
# ═══════════════════════════════════════════════════════════════════════

@overall_manager_required
def overall_dashboard(request):
    """Single dashboard for Overall Managers — all branches combined, with
    optional ?branch=<id> drill-down to a specific branch."""
    from datetime import timedelta
    from decimal import Decimal
    from branches.models import Branch

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    branches = Branch.objects.filter(is_active=True)

    # ── Optional branch filter ──
    selected_branch = None
    branch_id = request.GET.get('branch', '')
    if branch_id == 'all':
        # Explicitly selected "All" — show all branches, no filter
        selected_branch = None
    elif branch_id:
        try:
            selected_branch = Branch.objects.get(pk=branch_id, is_active=True)
            request.session['branch_id'] = selected_branch.pk
        except Branch.DoesNotExist:
            pass
    else:
        # No query param — arrived via topbar switcher redirect or direct nav.
        # Don't auto-select a branch; show "All" by default on this page.
        selected_branch = None

    # Build base querysets scoped to selected branch (or all)
    def scoped(qs, field='branch'):
        if selected_branch:
            return qs.filter(**{field: selected_branch})
        return qs

    # ── Summary totals ──
    paid_today = scoped(Order.objects.filter(status='paid', created_at__gte=today_start))
    today_sales = sum(o.get_total() for o in paid_today)
    today_count = paid_today.count()

    paid_week = scoped(Order.objects.filter(status='paid', created_at__gte=week_start))
    week_sales = sum(o.get_total() for o in paid_week)

    paid_month = scoped(Order.objects.filter(status='paid', created_at__gte=month_start))
    month_sales = sum(o.get_total() for o in paid_month)
    month_count = paid_month.count()

    active_orders = scoped(Order.objects.filter(status='active')).count()
    active_shifts = scoped(Shift.objects.filter(is_active=True)).count()

    low_stock_items = [
        i for i in scoped(InventoryItem.objects.all()) if i.is_low_stock
    ]

    table_qs = scoped(Table.objects.all())
    table_stats = {
        'total': table_qs.count(),
        'available': table_qs.filter(status='available').count(),
        'occupied': table_qs.filter(status='occupied').count(),
    }

    # ── Per-branch breakdown (only when viewing all) ──
    branch_data = []
    if not selected_branch:
        for branch in branches:
            bp_today = Order.objects.filter(
                branch=branch, status='paid', created_at__gte=today_start,
            )
            bp_month = Order.objects.filter(
                branch=branch, status='paid', created_at__gte=month_start,
            )
            b_active = Order.objects.filter(branch=branch, status='active').count()
            b_shifts = Shift.objects.filter(branch=branch, is_active=True).count()
            b_low = sum(
                1 for i in InventoryItem.objects.filter(branch=branch) if i.is_low_stock
            )
            b_balance = Decimal('0')
            for acct in Account.objects.filter(branch=branch):
                b_balance += acct.balance

            branch_data.append({
                'branch': branch,
                'today_sales': sum(o.get_total() for o in bp_today),
                'today_count': bp_today.count(),
                'month_sales': sum(o.get_total() for o in bp_month),
                'month_count': bp_month.count(),
                'active_orders': b_active,
                'active_shifts': b_shifts,
                'low_stock': b_low,
                'total_balance': b_balance,
            })
        branch_data.sort(key=lambda x: x['month_sales'], reverse=True)

    # ── Recent orders ──
    recent_orders = scoped(
        Order.objects.select_related('waiter', 'table', 'branch')
    ).exclude(waiter__is_superuser=True)[:10]

    # ── Top staff ──
    staff_perf = []
    for u in User.objects.filter(is_superuser=False, is_active=True):
        uorders = scoped(Order.objects.filter(
            waiter=u, status='paid', created_at__gte=month_start,
        ))
        total = sum(o.get_total() for o in uorders)
        if total > 0:
            staff_perf.append({
                'user': u,
                'order_count': uorders.count(),
                'total_sales': total,
            })
    staff_perf.sort(key=lambda x: x['total_sales'], reverse=True)

    # ── Top items ──
    top_items_qs = OrderItem.objects.filter(
        order__status='paid', order__created_at__gte=month_start,
    )
    if selected_branch:
        top_items_qs = top_items_qs.filter(order__branch=selected_branch)
    top_items = (
        top_items_qs
        .values('menu_item__title', 'menu_item__item_tier')
        .annotate(qty=Sum('quantity'))
        .order_by('-qty')[:10]
    )

    # ── Payment method breakdown ──
    payment_methods = {}
    for order in paid_month:
        method = order.get_payment_method_display() or 'Unknown'
        if method not in payment_methods:
            payment_methods[method] = {'count': 0, 'total': Decimal('0')}
        payment_methods[method]['count'] += 1
        payment_methods[method]['total'] += order.get_total()

    # ── Sales by category (this month) ──
    category_sales_qs = OrderItem.objects.filter(
        order__status='paid', order__created_at__gte=month_start,
    )
    if selected_branch:
        category_sales_qs = category_sales_qs.filter(order__branch=selected_branch)
    category_sales = (
        category_sales_qs
        .values('menu_item__category__name')
        .annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum(F('unit_price') * F('quantity')),
        )
        .order_by('-total_revenue')
    )

    # ── Account balances ──
    acct_qs = scoped(Account.objects.all())
    account_data = []
    total_balance = Decimal('0')
    for acct in acct_qs:
        bal = acct.balance
        total_balance += bal
        account_data.append({'account': acct, 'balance': bal})

    # ── Per-branch account balances (when viewing all) ──
    branch_accounts = []
    if not selected_branch:
        for branch in branches:
            b_accounts = []
            b_total = Decimal('0')
            for acct in Account.objects.filter(branch=branch):
                bal = acct.balance
                b_total += bal
                b_accounts.append({'name': acct.get_account_type_display(), 'balance': bal})
            branch_accounts.append({
                'branch': branch,
                'accounts': b_accounts,
                'total': b_total,
            })

    # ── Active shifts detail ──
    active_shift_list = scoped(
        Shift.objects.filter(is_active=True).select_related('waiter', 'branch')
    ).exclude(waiter__is_superuser=True)

    context = {
        'branches': branches,
        'selected_branch': selected_branch,
        'branch_data': branch_data,
        'today_sales': today_sales,
        'today_count': today_count,
        'week_sales': week_sales,
        'month_sales': month_sales,
        'month_count': month_count,
        'active_orders': active_orders,
        'active_shifts': active_shifts,
        'low_stock_items': low_stock_items[:10],
        'low_stock_count': len(low_stock_items),
        'table_stats': table_stats,
        'recent_orders': recent_orders,
        'staff_performance': staff_perf[:10],
        'top_items': top_items,
        'payment_methods': payment_methods,
        'account_data': account_data,
        'total_balance': total_balance,
        'branch_accounts': branch_accounts,
        'category_sales': category_sales,
        'active_shift_list': active_shift_list,
        'branch_count': branches.count(),
    }
    return render(request, 'administration/overall_dashboard.html', context)
