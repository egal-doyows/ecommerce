import io
from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as tz

from administration.models import Account, Transaction
from menu.models import RestaurantSettings

from .models import Expense, ExpenseCategory

# Managers can self-approve expenses below this threshold
MANAGER_AUTO_APPROVE_LIMIT = Decimal('20000')


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _is_admin_user(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )


def _is_manager(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name='Manager').exists()
    )


def staff_required(view_func):
    """Managers and Supervisors can access expenses."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_admin_user(request.user):
            messages.error(request, 'You do not have permission to access expenses.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def manager_only(view_func):
    """Only managers can approve / reject / delete expenses."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager(request.user):
            messages.error(request, 'Only managers can perform this action.')
            return redirect('expense-list')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# Account transaction helper
# ---------------------------------------------------------------------------

PAYMENT_TO_ACCOUNT = {
    'cash': 'cash',
    'mpesa': 'mpesa',
    'bank': 'bank',
}


def _record_expense_transaction(expense, user=None):
    """Create a debit transaction against the relevant financial account."""
    account_type = PAYMENT_TO_ACCOUNT.get(expense.payment_method)
    if not account_type:
        return None
    account = Account.get_by_type(account_type)
    return Transaction.objects.create(
        account=account,
        transaction_type='debit',
        amount=expense.amount,
        description=f'Expense {expense.expense_number} — {expense.description}',
        reference_type='expense',
        reference_id=expense.pk,
        created_by=user,
    )


def _reverse_expense_transaction(expense):
    """Remove the debit transaction when an expense is deleted or rejected."""
    Transaction.objects.filter(
        reference_type='expense',
        reference_id=expense.pk,
    ).delete()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@staff_required
def expense_list(request):
    qs = Expense.objects.select_related('category', 'recorded_by', 'approved_by')

    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    category_filter = request.GET.get('category', '')
    payment_filter = request.GET.get('payment', '')
    status_filter = request.GET.get('status', '')
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
    if category_filter:
        qs = qs.filter(category_id=category_filter)
    if payment_filter:
        qs = qs.filter(payment_method=payment_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(
            models.Q(description__icontains=search)
            | models.Q(vendor__icontains=search)
            | models.Q(receipt_number__icontains=search)
        )

    # Count pending for the badge
    pending_count = Expense.objects.filter(status='pending').count()

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    restaurant = RestaurantSettings.load()
    categories = ExpenseCategory.objects.filter(is_active=True)

    return render(request, 'expenses/expense_list.html', {
        'expenses': page_obj,
        'page_obj': page_obj,
        'date_from': date_from,
        'date_to': date_to,
        'category_filter': category_filter,
        'payment_filter': payment_filter,
        'status_filter': status_filter,
        'search': search,
        'categories': categories,
        'payment_choices': Expense.PAYMENT_METHOD_CHOICES,
        'status_choices': Expense.STATUS_CHOICES,
        'pending_count': pending_count,
        'restaurant': restaurant,
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@staff_required
def expense_create(request):
    restaurant = RestaurantSettings.load()
    categories = ExpenseCategory.objects.filter(is_active=True)
    user_is_manager = _is_manager(request.user)

    if request.method == 'POST':
        category_pk = request.POST.get('category', '')
        description = request.POST.get('description', '').strip()
        amount_str = request.POST.get('amount', '0')
        date_str = request.POST.get('date', '')
        payment_method = request.POST.get('payment_method', 'cash')
        receipt_number = request.POST.get('receipt_number', '').strip()
        vendor = request.POST.get('vendor', '').strip()
        recurring = request.POST.get('recurring', 'none')
        notes = request.POST.get('notes', '').strip()

        if not description:
            messages.error(request, 'Description is required.')
            return redirect('expense-create')

        try:
            amount = Decimal(amount_str)
        except Exception:
            amount = Decimal('0')

        if amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('expense-create')

        try:
            expense_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            expense_date = tz.now().date()

        category = None
        if category_pk:
            try:
                category = ExpenseCategory.objects.get(pk=category_pk)
            except ExpenseCategory.DoesNotExist:
                pass

        # Managers auto-approve under threshold; otherwise pending
        if user_is_manager and amount < MANAGER_AUTO_APPROVE_LIMIT:
            status = 'approved'
            approved_by = request.user
        else:
            status = 'pending'
            approved_by = None

        expense = Expense.objects.create(
            category=category,
            description=description,
            amount=amount,
            date=expense_date,
            payment_method=payment_method,
            receipt_number=receipt_number,
            vendor=vendor,
            recurring=recurring,
            notes=notes,
            status=status,
            recorded_by=request.user,
            approved_by=approved_by,
        )

        if status == 'approved':
            _record_expense_transaction(expense, user=request.user)
            messages.success(request, f'Expense {expense.expense_number} approved automatically.')
        else:
            messages.success(request, f'Expense request {expense.expense_number} submitted for approval.')

        return redirect('expense-detail', pk=expense.pk)

    # Account balances for JS validation
    account_balances = {}
    for pm_code, acct_type in PAYMENT_TO_ACCOUNT.items():
        acct = Account.get_by_type(acct_type)
        account_balances[pm_code] = str(acct.balance)

    return render(request, 'expenses/expense_create.html', {
        'categories': categories,
        'payment_choices': Expense.PAYMENT_METHOD_CHOICES,
        'recurring_choices': Expense.RECURRING_CHOICES,
        'today': tz.now().date().isoformat(),
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
        'user_is_manager': user_is_manager,
        'auto_approve_limit': MANAGER_AUTO_APPROVE_LIMIT,
        'account_balances_json': account_balances,
    })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@staff_required
def expense_detail(request, pk):
    expense = get_object_or_404(
        Expense.objects.select_related('category', 'recorded_by', 'approved_by'),
        pk=pk,
    )
    restaurant = RestaurantSettings.load()

    # Get balance of the account this expense would debit
    account_balance = None
    if expense.status == 'pending':
        acct_type = PAYMENT_TO_ACCOUNT.get(expense.payment_method)
        if acct_type:
            account_balance = Account.get_by_type(acct_type).balance

    return render(request, 'expenses/expense_detail.html', {
        'expense': expense,
        'account_balance': account_balance,
        'is_manager': _is_manager(request.user),
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


# ---------------------------------------------------------------------------
# Edit (only pending expenses, or managers can edit any)
# ---------------------------------------------------------------------------

@staff_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    restaurant = RestaurantSettings.load()
    categories = ExpenseCategory.objects.filter(is_active=True)
    user_is_manager = _is_manager(request.user)

    # Supervisors can only edit their own pending expenses
    if not user_is_manager:
        if expense.status != 'pending' or expense.recorded_by != request.user:
            messages.error(request, 'You can only edit your own pending expense requests.')
            return redirect('expense-detail', pk=pk)

    if request.method == 'POST':
        old_amount = expense.amount
        old_payment = expense.payment_method
        was_approved = expense.status == 'approved'

        category_pk = request.POST.get('category', '')
        expense.description = request.POST.get('description', '').strip()
        try:
            expense.amount = Decimal(request.POST.get('amount', '0'))
        except Exception:
            messages.error(request, 'Invalid amount.')
            return redirect('expense-edit', pk=pk)

        if expense.amount <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('expense-edit', pk=pk)

        date_str = request.POST.get('date', '')
        try:
            expense.date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

        expense.payment_method = request.POST.get('payment_method', expense.payment_method)
        expense.receipt_number = request.POST.get('receipt_number', '').strip()
        expense.vendor = request.POST.get('vendor', '').strip()
        expense.recurring = request.POST.get('recurring', 'none')
        expense.notes = request.POST.get('notes', '').strip()

        if category_pk:
            try:
                expense.category = ExpenseCategory.objects.get(pk=category_pk)
            except ExpenseCategory.DoesNotExist:
                expense.category = None
        else:
            expense.category = None

        expense.save()

        # Update financial transaction if approved and amount/payment changed
        if was_approved and (expense.amount != old_amount or expense.payment_method != old_payment):
            _reverse_expense_transaction(expense)
            _record_expense_transaction(expense, user=request.user)

        messages.success(request, f'Expense {expense.expense_number} updated.')
        return redirect('expense-detail', pk=expense.pk)

    return render(request, 'expenses/expense_edit.html', {
        'expense': expense,
        'categories': categories,
        'payment_choices': Expense.PAYMENT_METHOD_CHOICES,
        'recurring_choices': Expense.RECURRING_CHOICES,
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------

@manager_only
def expense_approve(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST' and expense.status == 'pending':
        if expense.recorded_by == request.user:
            messages.error(request, 'You cannot approve your own expense request.')
            return redirect('expense-detail', pk=pk)

        expense.status = 'approved'
        expense.approved_by = request.user
        expense.rejection_reason = ''
        expense.save()

        _record_expense_transaction(expense, user=request.user)

        messages.success(request, f'Expense {expense.expense_number} approved.')
    return redirect('expense-detail', pk=pk)


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

@manager_only
def expense_reject(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST' and expense.status == 'pending':
        if expense.recorded_by == request.user:
            messages.error(request, 'You cannot reject your own expense request.')
            return redirect('expense-detail', pk=pk)

        reason = request.POST.get('rejection_reason', '').strip()
        expense.status = 'rejected'
        expense.approved_by = request.user
        expense.rejection_reason = reason
        expense.save()

        messages.success(request, f'Expense {expense.expense_number} rejected.')
    return redirect('expense-detail', pk=pk)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@manager_only
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST':
        ref = expense.expense_number
        if expense.status == 'approved':
            _reverse_expense_transaction(expense)
        expense.delete()
        messages.success(request, f'Expense {ref} deleted.')
        return redirect('expense-list')
    return redirect('expense-detail', pk=pk)


# ---------------------------------------------------------------------------
# Summary / Analytics (only counts approved expenses)
# ---------------------------------------------------------------------------

@staff_required
def expense_summary(request):
    restaurant = RestaurantSettings.load()

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

    qs = Expense.objects.filter(
        date__gte=d_from, date__lte=d_to, status='approved',
    ).select_related('category')

    total_amount = qs.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    total_count = qs.count()

    # Pending stats
    pending_qs = Expense.objects.filter(
        date__gte=d_from, date__lte=d_to, status='pending',
    )
    pending_count = pending_qs.count()
    pending_amount = pending_qs.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    # By category
    by_category = []
    for cat in ExpenseCategory.objects.filter(is_active=True):
        cat_expenses = qs.filter(category=cat)
        cat_total = cat_expenses.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
        cat_count = cat_expenses.count()
        if cat_count > 0:
            by_category.append({
                'name': cat.name,
                'count': cat_count,
                'total': cat_total,
                'pct': round(cat_total / total_amount * 100, 1) if total_amount else 0,
            })
    # Uncategorised
    uncat = qs.filter(category__isnull=True)
    uncat_total = uncat.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    if uncat.count() > 0:
        by_category.append({
            'name': 'Uncategorised',
            'count': uncat.count(),
            'total': uncat_total,
            'pct': round(uncat_total / total_amount * 100, 1) if total_amount else 0,
        })
    by_category.sort(key=lambda x: x['total'], reverse=True)

    # By payment method
    by_payment = []
    for code, label in Expense.PAYMENT_METHOD_CHOICES:
        pm_expenses = qs.filter(payment_method=code)
        pm_total = pm_expenses.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
        pm_count = pm_expenses.count()
        if pm_count > 0:
            by_payment.append({
                'method': label,
                'count': pm_count,
                'total': pm_total,
                'pct': round(pm_total / total_amount * 100, 1) if total_amount else 0,
            })
    by_payment.sort(key=lambda x: x['total'], reverse=True)

    # Top vendors
    vendor_totals = {}
    for e in qs.exclude(vendor=''):
        v = e.vendor
        if v not in vendor_totals:
            vendor_totals[v] = {'vendor': v, 'total': Decimal('0'), 'count': 0}
        vendor_totals[v]['total'] += e.amount
        vendor_totals[v]['count'] += 1
    top_vendors = sorted(vendor_totals.values(), key=lambda x: x['total'], reverse=True)[:10]

    return render(request, 'expenses/expense_summary.html', {
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
        'date_from': date_from,
        'date_to': date_to,
        'total_amount': total_amount,
        'total_count': total_count,
        'pending_count': pending_count,
        'pending_amount': pending_amount,
        'by_category': by_category,
        'by_payment': by_payment,
        'top_vendors': top_vendors,
    })


# ---------------------------------------------------------------------------
# PDF (only for approved expenses)
# ---------------------------------------------------------------------------

@staff_required
def expense_pdf(request, pk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    expense = get_object_or_404(
        Expense.objects.select_related('category', 'recorded_by', 'approved_by'),
        pk=pk,
    )
    restaurant = RestaurantSettings.load()
    cs = restaurant.currency_symbol

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('DocTitle', parent=styles['Heading1'],
                              fontSize=18, textColor=colors.HexColor('#6366f1'),
                              spaceAfter=4))
    styles.add(ParagraphStyle('SubInfo', parent=styles['Normal'],
                              fontSize=9, textColor=colors.grey))

    elements = []

    # Header
    from menu.pdf_utils import restaurant_logo_image
    logo = restaurant_logo_image(restaurant)
    if logo:
        elements.append(logo)
        elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(restaurant.name, styles['Heading2']))
    elements.append(Paragraph('EXPENSE VOUCHER', styles['DocTitle']))
    elements.append(Spacer(1, 6 * mm))

    # Status banner
    status_text = expense.get_status_display()
    if expense.status == 'approved':
        status_color = colors.HexColor('#28a745')
    elif expense.status == 'rejected':
        status_color = colors.HexColor('#dc3545')
    else:
        status_color = colors.HexColor('#d97706')

    status_data = [[f'Status: {status_text}']]
    status_table = Table(status_data, colWidths=[160 * mm])
    status_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), status_color),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(status_table)
    elements.append(Spacer(1, 6 * mm))

    # Details table
    data = [
        ['Expense Ref:', expense.expense_number, 'Date:', expense.date.strftime('%d %b %Y')],
        ['Category:', expense.category.name if expense.category else '—',
         'Payment:', expense.get_payment_method_display()],
        ['Vendor:', expense.vendor or '—',
         'Receipt #:', expense.receipt_number or '—'],
        ['Requested by:', expense.recorded_by.username if expense.recorded_by else '—',
         'Recorded at:', expense.created_at.strftime('%d %b %Y, %H:%M')],
    ]

    if expense.approved_by:
        label = 'Approved by:' if expense.status == 'approved' else 'Rejected by:'
        data.append([label, expense.approved_by.username, '', ''])

    if expense.recurring != 'none':
        data.append(['Recurring:', expense.get_recurring_display(), '', ''])

    meta_table = Table(data, colWidths=[28 * mm, 52 * mm, 28 * mm, 52 * mm])
    meta_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.grey),
        ('TEXTCOLOR', (2, 0), (2, -1), colors.grey),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 6 * mm))

    # Description & amount
    desc_data = [
        ['Description', f'Amount ({cs})'],
        [expense.description, f'{expense.amount:,.2f}'],
    ]
    desc_table = Table(desc_data, colWidths=[120 * mm, 40 * mm])
    desc_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366f1')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#6366f1')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('FONTNAME', (1, 1), (1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (1, 1), (1, 1), 14),
    ]))
    elements.append(desc_table)

    if expense.notes:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph(f'<b>Notes:</b> {expense.notes}', styles['Normal']))

    if expense.rejection_reason:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph(
            f'<b>Rejection Reason:</b> {expense.rejection_reason}', styles['Normal']))

    elements.append(Spacer(1, 12 * mm))

    # Signature lines
    sig_data = [
        ['Requested by', 'Approved by'],
        ['', ''],
        ['_________________________', '_________________________'],
        [expense.recorded_by.username if expense.recorded_by else '________________',
         expense.approved_by.username if expense.approved_by else '________________'],
    ]
    sig_table = Table(sig_data, colWidths=[80 * mm, 80 * mm])
    sig_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.grey),
        ('TOPPADDING', (0, 2), (-1, 2), 20),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(sig_table)

    # Footer
    elements.append(Spacer(1, 8 * mm))
    elements.append(Paragraph(
        f'Generated on {tz.now().strftime("%d %b %Y, %H:%M")} — {restaurant.name}',
        styles['SubInfo'],
    ))

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{expense.expense_number}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Category management
# ---------------------------------------------------------------------------

@manager_only
def category_list(request):
    categories = ExpenseCategory.objects.annotate(
        expense_count=models.Count('expenses', filter=models.Q(expenses__status='approved')),
        total=models.Sum('expenses__amount', filter=models.Q(expenses__status='approved')),
    )
    restaurant = RestaurantSettings.load()
    return render(request, 'expenses/category_list.html', {
        'categories': categories,
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


@manager_only
def category_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            messages.error(request, 'Category name is required.')
            return redirect('expense-category-create')
        if ExpenseCategory.objects.filter(name__iexact=name).exists():
            messages.error(request, 'A category with that name already exists.')
            return redirect('expense-category-create')
        ExpenseCategory.objects.create(name=name, description=description)
        messages.success(request, f'Category "{name}" created.')
        return redirect('expense-category-list')

    return render(request, 'expenses/category_form.html', {
        'title': 'Add Category',
        'action': 'Create',
    })


@manager_only
def category_edit(request, pk):
    category = get_object_or_404(ExpenseCategory, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        if not name:
            messages.error(request, 'Category name is required.')
            return redirect('expense-category-edit', pk=pk)
        dup = ExpenseCategory.objects.filter(name__iexact=name).exclude(pk=pk)
        if dup.exists():
            messages.error(request, 'A category with that name already exists.')
            return redirect('expense-category-edit', pk=pk)
        category.name = name
        category.description = description
        category.is_active = is_active
        category.save()
        messages.success(request, f'Category "{name}" updated.')
        return redirect('expense-category-list')

    return render(request, 'expenses/category_form.html', {
        'title': 'Edit Category',
        'action': 'Save',
        'category': category,
    })
