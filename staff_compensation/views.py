from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages

from core.auth import has_full_access
from .models import StaffCompensation, StaffBankDetails, PaymentRecord, AdvanceRequest, Payroll, PayrollLine, generate_past_month_records, generate_current_month_record, preview_payroll, generate_payroll
from .forms import CompensationForm, StaffBankDetailsForm, PaymentDisbursementForm, AdvanceRequestForm, ManagerAdvanceRequestForm, AdvanceReviewForm


from core.permissions import is_manager as _can_manage_compensation, is_overall_manager


def _base_template(user):
    """Return admin base for Owner/superuser, POS base for others."""
    if user.is_superuser or user.groups.filter(name='Owner').exists():
        return 'administration/base.html'
    return 'menu/base.html'


def _get_commission_staff(branch=None):
    """Return queryset of users in groups that earn commission (Front Service + Attendant + Marketing)."""
    qs = User.objects.filter(
        groups__name__in=['Front Service', 'Attendant', 'Marketing'], is_active=True,
    )
    if branch:
        from branches.models import UserBranch
        branch_user_ids = UserBranch.objects.filter(branch=branch).values_list('user_id', flat=True)
        qs = qs.filter(pk__in=branch_user_ids)
    return qs.select_related('compensation').prefetch_related('groups')


@login_required(login_url='my-login')
def compensation_overview(request):
    """Manager view: list staff compensation. Owners see all employees (salary + commission)."""
    if not _can_manage_compensation(request.user):
        return redirect('dashboard')

    from core.auth import is_admin_role
    is_overall = is_overall_manager(request.user)
    is_owner = is_admin_role(request.user)

    from branches.models import Branch, UserBranch
    from django.db.models import Sum

    show_all = is_owner or is_overall or request.user.is_superuser

    if show_all:
        # Owner/superuser/overall: show ALL employees with compensation
        staff = User.objects.filter(
            is_superuser=False, is_active=True, compensation__isnull=False,
        ).select_related('compensation').prefetch_related('groups')
        if request.branch:
            branch_user_ids = UserBranch.objects.filter(branch=request.branch).values_list('user_id', flat=True)
            staff = staff.filter(pk__in=branch_user_ids)
    else:
        staff = _get_commission_staff(branch=request.branch)

    # Auto-generate payment records for commission-earning staff
    for member in staff:
        comp = getattr(member, 'compensation', None)
        if comp and comp.earns_commission:
            generate_past_month_records(member)
            generate_current_month_record(member)

    # Build user→branch mapping
    user_ids = [m.pk for m in staff]
    user_branches = {}
    for ub in UserBranch.objects.filter(user_id__in=user_ids).select_related('branch'):
        user_branches[ub.user_id] = ub.branch.name

    # Build staff_data with branch info for grouping
    staff_data = []
    for member in staff:
        comp = getattr(member, 'compensation', None)
        data = {'user': member, 'compensation': comp, 'branch_name': user_branches.get(member.pk, '')}
        if comp and comp.earns_commission:
            data['current_month_commission'] = comp.get_current_month_commission()
            data['current_month_sales'] = comp.get_current_month_sales()
            data['current_month_eligible_sales'] = comp.get_current_month_eligible_sales()
        # Find the latest pending payment for this staff member
        pending_qs = PaymentRecord.objects.filter(staff=member, status='pending')
        data['pending_payment'] = pending_qs.order_by('-period_end').first()
        data['outstanding'] = pending_qs.aggregate(total=Sum('amount'))['total'] or 0
        staff_data.append(data)

    # Sort by branch name for grouping
    if show_all:
        staff_data.sort(key=lambda x: x['branch_name'] or 'zzz')

    now = timezone.now()

    context = {
        'staff_data': staff_data,
        'current_month': now.strftime('%B %Y'),
        'is_overall': show_all,
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/overview.html', context)


@login_required(login_url='my-login')
def staff_detail(request, user_id):
    """Manager view: detailed compensation breakdown for a staff member."""
    if not _can_manage_compensation(request.user):
        return redirect('dashboard')

    staff_user = get_object_or_404(User, pk=user_id)

    # Verify staff belongs to current branch (Owner/Overall Managers can view any)
    if request.branch and not has_full_access(request.user) and not request.user.groups.filter(name='Overall Manager').exists():
        from branches.models import UserBranch
        if not UserBranch.objects.filter(user=staff_user, branch=request.branch).exists():
            messages.error(request, 'Staff member not in your branch.')
            return redirect('compensation-overview')

    compensation = getattr(staff_user, 'compensation', None)
    bank_details = getattr(staff_user, 'bank_details', None)

    # Auto-generate payment records for past and current months
    generate_past_month_records(staff_user)
    generate_current_month_record(staff_user)

    now = timezone.now()

    context = {
        'staff_user': staff_user,
        'compensation': compensation,
        'bank_details': bank_details,
        'current_month': now.strftime('%B %Y'),
    }

    if compensation:
        context['current_month_sales'] = compensation.get_current_month_sales()
        context['current_month_orders'] = compensation.get_current_month_order_count()
        if compensation.earns_commission:
            context['current_month_commission'] = compensation.get_current_month_commission()
            context['current_month_eligible_sales'] = compensation.get_current_month_eligible_sales()
            # Daily breakdown for current month
            start, end = compensation.get_current_month_range()
            context['daily_breakdown'] = compensation.get_daily_breakdown(start, end)

    payment_history = PaymentRecord.objects.filter(staff=staff_user).order_by('-period_end')
    context['payment_history'] = payment_history

    # Outstanding = sum of all pending payment amounts
    from django.db.models import Sum
    outstanding = payment_history.filter(status='pending').aggregate(
        total=Sum('amount')
    )['total'] or 0
    total_paid = payment_history.filter(status='paid').aggregate(
        total=Sum('amount')
    )['total'] or 0
    context['outstanding_commission'] = outstanding
    context['total_paid'] = total_paid
    context['base_template'] = _base_template(request.user)

    return render(request, 'staff_compensation/detail.html', context)


@login_required(login_url='my-login')
def edit_compensation(request, user_id):
    """Owner/Superuser: edit a staff member's compensation settings."""
    if not has_full_access(request.user):
        messages.error(request, 'Only the administrator can perform this action.')
        return redirect('admin-dashboard')

    staff_user = get_object_or_404(User, pk=user_id)

    try:
        instance = staff_user.compensation
    except StaffCompensation.DoesNotExist:
        instance = None

    if request.method == 'POST':
        form = CompensationForm(request.POST, instance=instance)
        if form.is_valid():
            comp = form.save(commit=False)
            comp.user = staff_user
            comp.save()
            messages.success(request, f'Compensation updated for {staff_user.username}.')
            return redirect('compensation-detail', user_id=staff_user.pk)
    else:
        form = CompensationForm(instance=instance)

    context = {
        'form': form,
        'staff_user': staff_user,
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/edit.html', context)


@login_required(login_url='my-login')
def payment_list(request):
    """Manager view: payment records for commission-earning staff."""
    if not _can_manage_compensation(request.user):
        return redirect('dashboard')

    commission_staff = _get_commission_staff(branch=request.branch)

    # Auto-generate for commission-earning staff
    for user in commission_staff:
        generate_past_month_records(user)
        generate_current_month_record(user)

    front_service_ids = commission_staff.values_list('pk', flat=True)
    payments = PaymentRecord.objects.filter(
        staff_id__in=front_service_ids,
    ).select_related('staff', 'branch')

    status_filter = request.GET.get('status')
    if status_filter in ('pending', 'paid'):
        payments = payments.filter(status=status_filter)

    context = {
        'payments': payments,
        'status_filter': status_filter,
        'is_overall': False,
        'branches': [],
        'branch_filter': '',
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/payments.html', context)


@login_required(login_url='my-login')
def pay_staff(request, pk):
    """Owner/Superuser: pay a pending payment — choose method (cash/bank/mpesa)."""
    if not has_full_access(request.user):
        messages.error(request, 'Only the administrator can perform this action.')
        return redirect('admin-dashboard')

    payment = get_object_or_404(PaymentRecord, pk=pk, status='pending')

    remaining = payment.remaining

    if request.method == 'POST':
        form = PaymentDisbursementForm(request.POST, remaining_amount=remaining)
        if form.is_valid():
            account = form.cleaned_data['account']
            pay_amount = form.cleaned_data['amount']
            notes = form.cleaned_data.get('notes', '')
            if notes:
                payment.notes = notes
            payment.mark_paid(method='cash', pay_amount=pay_amount)

            # Record debit in accounts
            from administration.models import record_staff_payment
            record_staff_payment(payment, account=account, created_by=request.user, amount=pay_amount)

            from menu.models import RestaurantSettings
            symbol = RestaurantSettings.load().currency_symbol
            if payment.status == 'paid':
                messages.success(
                    request,
                    f'{symbol} {pay_amount:,.2f} paid to {payment.staff.username} — fully paid.',
                )
            else:
                messages.success(
                    request,
                    f'{symbol} {pay_amount:,.2f} paid to {payment.staff.username} — '
                    f'{symbol} {payment.remaining:,.2f} remaining.',
                )
            return redirect('compensation-detail', payment.staff.pk)
    else:
        form = PaymentDisbursementForm(remaining_amount=remaining)

    # Build account data with balances for the template
    from administration.models import Account
    accounts = Account.objects.filter(is_active=True, account_type='cash', branch=request.branch)
    account_list = []
    for a in accounts:
        bal = a.balance
        account_list.append({
            'pk': a.pk,
            'name': a.name,
            'account_type': a.account_type,
            'balance': bal,
            'sufficient': bal >= remaining,
        })

    context = {
        'form': form,
        'payment': payment,
        'account_list': account_list,
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/pay.html', context)


@login_required(login_url='my-login')
def bank_details_edit(request, user_id):
    """Owner/Superuser: add or edit a staff member's bank details."""
    if not has_full_access(request.user):
        messages.error(request, 'Only the administrator can perform this action.')
        return redirect('admin-dashboard')

    staff_user = get_object_or_404(User, pk=user_id)
    try:
        instance = staff_user.bank_details
    except StaffBankDetails.DoesNotExist:
        instance = None

    next_url = request.GET.get('next', '') or request.POST.get('next', '')

    if request.method == 'POST':
        form = StaffBankDetailsForm(request.POST, instance=instance)
        if form.is_valid():
            bank = form.save(commit=False)
            bank.user = staff_user
            bank.save()
            messages.success(request, f'Bank details saved for {staff_user.username}.')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect('compensation-detail', user_id=staff_user.pk)
    else:
        form = StaffBankDetailsForm(instance=instance)

    context = {
        'form': form,
        'staff_user': staff_user,
        'next_url': next_url,
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/bank_details.html', context)


@login_required(login_url='my-login')
def my_earnings(request):
    """Staff view: see own earnings and compensation details."""
    compensation = getattr(request.user, 'compensation', None)

    # Auto-generate payment records
    generate_past_month_records(request.user)
    generate_current_month_record(request.user)

    now = timezone.now()

    context = {
        'compensation': compensation,
        'current_month': now.strftime('%B %Y'),
    }

    if compensation:
        context['current_month_sales'] = compensation.get_current_month_sales()
        context['current_month_orders'] = compensation.get_current_month_order_count()
        if compensation.earns_commission:
            context['current_month_commission'] = compensation.get_current_month_commission()
            context['current_month_eligible_sales'] = compensation.get_current_month_eligible_sales()
            start, end = compensation.get_current_month_range()
            context['daily_breakdown'] = compensation.get_daily_breakdown(start, end)

    context['payment_history'] = PaymentRecord.objects.filter(staff=request.user).order_by('-period_end')

    return render(request, 'staff_compensation/my_earnings.html', context)


# ═══════════════════════════════════════════════════════════════════════
#  ADVANCE REQUESTS
# ═══════════════════════════════════════════════════════════════════════

def _is_branch_manager(user):
    return user.groups.filter(name='Branch Manager').exists()


def _can_approve_advance(user):
    """Only Owner and Overall Manager can approve advances."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Owner', 'Overall Manager']).exists()


@login_required(login_url='my-login')
def advance_request_list(request):
    """List advance requests visible to the current user."""
    user = request.user
    from core.auth import has_full_access

    is_approver = has_full_access(user) or user.groups.filter(name__in=['Overall Manager', 'Branch Manager']).exists()

    if is_approver:
        # Pending requests from others + all own requests (any status)
        from django.db.models import Q
        others_pending = Q(status='pending') & ~Q(employee=user)
        own_requests = Q(employee=user)
        qs = AdvanceRequest.objects.filter(others_pending | own_requests)
        if not has_full_access(user) and not user.groups.filter(name='Overall Manager').exists():
            qs = qs.filter(branch=request.branch)
    else:
        # Regular staff: see all their own requests (any status)
        qs = AdvanceRequest.objects.filter(employee=user)

    status_filter = request.GET.get('status')
    if status_filter in ('pending', 'approved', 'rejected', 'disbursed'):
        qs = qs.filter(status=status_filter)

    qs = qs.select_related('employee', 'requested_by', 'reviewed_by', 'branch')

    context = {
        'advance_requests': qs,
        'status_filter': status_filter,
        'can_approve': _can_approve_advance(user),
        'is_branch_manager': _is_branch_manager(user),
        'is_approver': is_approver,
        'base_template': _base_template(user),
    }
    return render(request, 'staff_compensation/advance_list.html', context)


@login_required(login_url='my-login')
def advance_request_create(request):
    """Staff submits their own advance request."""
    user = request.user
    comp = getattr(user, 'compensation', None)

    if not comp or comp.compensation_type == 'commission':
        messages.error(request, 'Only salaried employees can request advances.')
        return redirect('dashboard')

    if request.method == 'POST':
        form = AdvanceRequestForm(request.POST, salary_amount=comp.salary_amount)
        if form.is_valid():
            advance = form.save(commit=False)
            advance.employee = user
            advance.requested_by = user
            advance.branch = request.branch
            advance.save()
            messages.success(request, 'Advance request submitted.')
            return redirect('advance-list')
    else:
        form = AdvanceRequestForm(salary_amount=comp.salary_amount)

    from menu.models import RestaurantSettings
    context = {
        'form': form,
        'title': 'Request Salary Advance',
        'salary_amount': comp.salary_amount,
        'currency': RestaurantSettings.load().currency_symbol,
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/advance_form.html', context)


@login_required(login_url='my-login')
def advance_request_for_staff(request):
    """Branch Manager submits advance request on behalf of an attendant."""
    if not _is_branch_manager(request.user) and not _can_approve_advance(request.user):
        messages.error(request, 'You do not have permission to perform this action.')
        return redirect('advance-list')

    if request.method == 'POST':
        form = ManagerAdvanceRequestForm(request.POST, branch=request.branch)
        if form.is_valid():
            advance = form.save(commit=False)
            advance.employee = form.cleaned_data['employee']
            advance.requested_by = request.user
            advance.branch = request.branch
            advance.save()
            messages.success(request, f'Advance request submitted for {advance.employee.username}.')
            return redirect('advance-list')
    else:
        form = ManagerAdvanceRequestForm(branch=request.branch)

    context = {
        'form': form,
        'title': 'Request Advance for Employee',
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/advance_form.html', context)


@login_required(login_url='my-login')
def advance_request_review(request, pk):
    """Owner/Overall Manager/Branch Manager reviews (approves/rejects) an advance request."""
    if not _can_approve_advance(request.user):
        messages.error(request, 'You do not have permission to review advance requests.')
        return redirect('advance-list')

    advance = get_object_or_404(AdvanceRequest, pk=pk, status='pending')

    # No one can approve their own request
    if advance.requested_by == request.user or advance.employee == request.user:
        messages.error(request, 'You cannot approve your own request.')
        return redirect('advance-list')

    if request.method == 'POST':
        form = AdvanceReviewForm(request.POST)
        if form.is_valid():
            advance.status = form.cleaned_data['action']
            advance.review_notes = form.cleaned_data['review_notes']
            advance.reviewed_by = request.user
            advance.reviewed_at = timezone.now()
            advance.save()
            action_label = 'approved' if advance.status == 'approved' else 'rejected'
            messages.success(request, f'Advance request {action_label}.')
            return redirect('advance-list')
    else:
        form = AdvanceReviewForm()

    from menu.models import RestaurantSettings
    context = {
        'form': form,
        'advance': advance,
        'currency': RestaurantSettings.load().currency_symbol,
        'base_template': _base_template(request.user),
    }
    return render(request, 'staff_compensation/advance_review.html', context)


@login_required(login_url='my-login')
def advance_cancel(request, pk):
    """Employee cancels their own pending advance request."""
    advance = get_object_or_404(AdvanceRequest, pk=pk)
    is_own = advance.employee == request.user or advance.requested_by == request.user
    if not is_own:
        messages.error(request, 'Only the requester can cancel an advance request.')
        return redirect('advance-list')
    if request.method == 'POST' and advance.status == 'pending':
        advance.status = 'cancelled'
        advance.save()
        messages.success(request, 'Advance request cancelled.')
    return redirect('advance-list')


@login_required(login_url='my-login')
def advance_pdf(request, pk):
    """Generate a professional PDF for an approved or rejected advance request."""
    import io
    import os

    from django.conf import settings as django_settings
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    user = request.user

    # Only the employee themselves can download their advance PDF
    adv = get_object_or_404(
        AdvanceRequest.objects.select_related(
            'employee', 'requested_by', 'reviewed_by', 'branch',
        ),
        pk=pk, employee=user, status__in=['approved', 'rejected'],
    )

    from menu.models import RestaurantSettings
    restaurant = RestaurantSettings.load()
    currency = restaurant.currency_symbol
    is_approved = adv.status == 'approved'
    width, height = A4

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # ── Colours ──
    PRIMARY = colors.HexColor('#1b1f3b')
    ACCENT = colors.HexColor('#7c83ff')
    MUTED = colors.HexColor('#6b7280')
    BORDER = colors.HexColor('#e2e5ea')
    LIGHT_BG = colors.HexColor('#f8f9fb')
    SUCCESS = colors.HexColor('#16a34a')
    DANGER = colors.HexColor('#dc2626')

    status_color = SUCCESS if is_approved else DANGER
    status_bg = colors.HexColor('#dcfce7') if is_approved else colors.HexColor('#fef2f2')
    status_label = 'APPROVED' if is_approved else 'REJECTED'

    y = height - 30 * mm

    # ── Header band ──
    c.setFillColor(PRIMARY)
    c.rect(0, height - 26 * mm, width, 26 * mm, fill=True, stroke=False)

    logo_path = None
    if restaurant.logo:
        logo_path = os.path.join(django_settings.MEDIA_ROOT, restaurant.logo.name)
    if not logo_path or not os.path.exists(logo_path):
        logo_path = os.path.join(django_settings.BASE_DIR, 'static', 'icons', 'sanityicon.png')
    if os.path.exists(logo_path):
        c.drawImage(logo_path, 18 * mm, height - 23 * mm, width=20 * mm, height=20 * mm,
                     preserveAspectRatio=True, mask='auto')

    c.setFillColor(colors.white)
    text_center = (40 * mm + (width - 55 * mm)) / 2
    c.setFont('Helvetica-Bold', 16)
    c.drawCentredString(text_center, height - 15 * mm, restaurant.name)
    c.setFont('Helvetica', 8)
    c.drawCentredString(text_center, height - 20 * mm, restaurant.phone or '')

    # Status stamp
    c.setFillColor(status_bg)
    c.roundRect(width - 55 * mm, height - 20 * mm, 40 * mm, 10 * mm, 3, fill=True, stroke=False)
    c.setFillColor(status_color)
    c.setFont('Helvetica-Bold', 10)
    c.drawCentredString(width - 35 * mm, height - 17.2 * mm, status_label)

    y = height - 38 * mm

    # ── Title ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(20 * mm, y, 'Salary Advance — ' + status_label.title())
    y -= 6 * mm
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.line(20 * mm, y, 90 * mm, y)
    y -= 10 * mm

    # ── Reference / Date ──
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    ref = f'Ref: ADV-{adv.pk:04d}'
    c.drawString(20 * mm, y, ref)
    date_str = adv.reviewed_at.strftime('%d %B %Y') if adv.reviewed_at else ''
    c.drawRightString(width - 20 * mm, y, f'Date: {date_str}')
    y -= 12 * mm

    # ── Employee info card ──
    emp_user = adv.employee
    emp_name = emp_user.get_full_name() or emp_user.username
    branch_name = adv.branch.name if adv.branch else '—'

    # Fetch HR profile with related position & department
    from hr.models import Employee
    try:
        hr_profile = Employee.objects.select_related(
            'position', 'department',
        ).get(user=emp_user)
    except Employee.DoesNotExist:
        hr_profile = None

    emp_id = hr_profile.employee_id if hr_profile else '—'
    position = hr_profile.position.title if hr_profile and hr_profile.position else '—'
    department = hr_profile.department.name if hr_profile and hr_profile.department else '—'

    card_h = 38 * mm
    c.setFillColor(LIGHT_BG)
    c.roundRect(20 * mm, y - card_h, width - 40 * mm, card_h, 4, fill=True, stroke=False)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.roundRect(20 * mm, y - card_h, width - 40 * mm, card_h, 4, fill=False, stroke=True)

    cx = 26 * mm
    cy = y - 8 * mm
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(cx, cy, emp_name)
    cy -= 6 * mm
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    c.drawString(cx, cy, f'Employee ID: {emp_id}')
    cy -= 5 * mm
    c.drawString(cx, cy, f'Position: {position}')
    cy -= 5 * mm
    c.drawString(cx, cy, f'Department: {department}')
    cy -= 5 * mm
    c.drawString(cx, cy, f'Branch: {branch_name}')

    y -= card_h + 12 * mm

    # ── Advance details table ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20 * mm, y, 'Advance Details')
    y -= 3 * mm

    table_x = 20 * mm
    table_w = width - 40 * mm
    row_h = 9 * mm

    # Header
    c.setFillColor(PRIMARY)
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 8)
    cols = [table_x + 3 * mm, table_x + 50 * mm, table_x + 100 * mm]
    c.drawString(cols[0], y - 6.2 * mm, 'AMOUNT REQUESTED')
    c.drawString(cols[1], y - 6.2 * mm, 'REQUESTED BY')
    c.drawString(cols[2], y - 6.2 * mm, 'DATE SUBMITTED')
    y -= row_h

    # Row
    c.setFillColor(colors.white)
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.rect(table_x, y - row_h, table_w, row_h, fill=False, stroke=True)

    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(cols[0], y - 6.5 * mm, f'{currency} {adv.amount:,.2f}')
    c.setFont('Helvetica', 9)
    req_by = adv.requested_by.get_full_name() or adv.requested_by.username if adv.requested_by else '—'
    if adv.requested_by != adv.employee:
        req_by += ' (on behalf)'
    c.drawString(cols[1], y - 6.2 * mm, req_by)
    c.drawString(cols[2], y - 6.2 * mm, adv.created_at.strftime('%d %b %Y, %H:%M'))
    y -= row_h + 10 * mm

    # ── Reason ──
    if adv.reason:
        c.setFillColor(PRIMARY)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(20 * mm, y, 'Reason for Advance')
        y -= 6 * mm
        c.setFont('Helvetica', 9)
        c.setFillColor(MUTED)
        lines = simpleSplit(adv.reason, 'Helvetica', 9, table_w)
        for line in lines[:6]:
            c.drawString(20 * mm, y, line)
            y -= 4.5 * mm
        y -= 6 * mm

    # ── Review info ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(20 * mm, y, 'Review Details')
    y -= 3 * mm

    app_h = 20 * mm
    review_bg = colors.HexColor('#f0fdf4') if is_approved else colors.HexColor('#fef2f2')
    review_border = colors.HexColor('#bbf7d0') if is_approved else colors.HexColor('#fecaca')
    c.setFillColor(review_bg)
    c.roundRect(20 * mm, y - app_h, table_w, app_h, 4, fill=True, stroke=False)
    c.setStrokeColor(review_border)
    c.setLineWidth(0.5)
    c.roundRect(20 * mm, y - app_h, table_w, app_h, 4, fill=False, stroke=True)

    ay = y - 7 * mm
    c.setFillColor(status_color)
    c.setFont('Helvetica-Bold', 9)
    reviewer_name = adv.reviewed_by.get_full_name() or adv.reviewed_by.username if adv.reviewed_by else '—'
    action_word = 'Approved' if is_approved else 'Rejected'
    c.drawString(26 * mm, ay, f'{action_word} by: {reviewer_name}')
    ay -= 5.5 * mm
    c.setFont('Helvetica', 8)
    c.setFillColor(MUTED)
    if adv.reviewed_at:
        c.drawString(26 * mm, ay, f'Date: {adv.reviewed_at.strftime("%d %B %Y at %H:%M")}')
    if adv.review_notes:
        ay -= 5 * mm
        c.drawString(26 * mm, ay, f'Notes: {adv.review_notes[:80]}')

    y -= app_h + 20 * mm

    # ── Signature lines ──
    sig_y = y
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.8)
    c.line(20 * mm, sig_y, 85 * mm, sig_y)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawString(20 * mm, sig_y - 5 * mm, 'Authorized Signature')
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(20 * mm, sig_y - 10 * mm, reviewer_name)

    c.setStrokeColor(BORDER)
    c.line(width - 85 * mm, sig_y, width - 20 * mm, sig_y)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawString(width - 85 * mm, sig_y - 5 * mm, 'Employee Signature')
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(width - 85 * mm, sig_y - 10 * mm, emp_name)

    # ── Footer ──
    c.setFillColor(BORDER)
    c.rect(0, 0, width, 14 * mm, fill=True, stroke=False)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 7)
    c.drawCentredString(width / 2, 6 * mm,
                        f'{restaurant.name}  •  This document was generated electronically and is valid without a physical signature')
    c.drawCentredString(width / 2, 2.5 * mm,
                        f'Generated on {timezone.now().strftime("%d %B %Y at %H:%M")}  •  {ref}')

    c.showPage()
    c.save()
    buf.seek(0)

    filename = f'advance_{adv.status}_{emp_id}_{adv.created_at.strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required(login_url='my-login')
def advance_verify(request):
    """Managers verify an advance request by entering its reference number."""
    user = request.user
    is_mgr = user.is_superuser or user.groups.filter(
        name__in=['Owner', 'Overall Manager', 'Branch Manager']
    ).exists()
    if not is_mgr:
        messages.error(request, 'You do not have permission to verify advances.')
        return redirect('dashboard')

    advance = None
    error = None
    ref_input = request.GET.get('ref', '').strip()

    if ref_input:
        # Accept formats: ADV-0002, 0002, 2, etc.
        import re
        match = re.search(r'(\d+)', ref_input)
        if match:
            pk = int(match.group(1))
            try:
                advance = AdvanceRequest.objects.select_related(
                    'employee', 'requested_by', 'reviewed_by', 'branch',
                ).get(pk=pk)
            except AdvanceRequest.DoesNotExist:
                error = f'No advance request found with reference "{ref_input}".'
        else:
            error = 'Invalid reference format. Use e.g. ADV-0002 or just 2.'

    # Fetch HR profile if advance found
    hr_profile = None
    if advance:
        from hr.models import Employee
        try:
            hr_profile = Employee.objects.select_related(
                'position', 'department', 'branch',
            ).get(user=advance.employee)
        except Employee.DoesNotExist:
            pass

    from menu.models import RestaurantSettings
    context = {
        'advance': advance,
        'hr_profile': hr_profile,
        'error': error,
        'ref_input': ref_input,
        'currency': RestaurantSettings.load().currency_symbol,
        'base_template': _base_template(user),
    }
    return render(request, 'staff_compensation/advance_verify.html', context)


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------

@login_required(login_url='my-login')
def payroll_list(request):
    """View payroll batches. Managers see all; staff see batches containing their lines."""
    user = request.user
    is_mgr = _can_manage_compensation(user)

    if is_mgr:
        qs = Payroll.objects.all()
        if not (is_overall_manager(user)):
            qs = qs.filter(branch=request.branch)
    else:
        qs = Payroll.objects.filter(lines__employee=user).distinct()

    # Filters
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    if month_filter and month_filter.isdigit():
        qs = qs.filter(month=int(month_filter))
    if year_filter and year_filter.isdigit():
        qs = qs.filter(year=int(year_filter))

    qs = qs.select_related('branch', 'generated_by')

    years = Payroll.objects.values_list('year', flat=True).distinct().order_by('-year')

    # Default generate form to previous month
    now = timezone.localdate()
    if now.month == 1:
        default_month = 12
        default_year = now.year - 1
    else:
        default_month = now.month - 1
        default_year = now.year

    from menu.models import RestaurantSettings
    context = {
        'payrolls': qs,
        'is_manager': is_mgr,
        'month_filter': month_filter,
        'year_filter': year_filter,
        'years': years,
        'currency': RestaurantSettings.load().currency_symbol,
        'base_template': _base_template(user),
        'default_month': default_month,
        'default_year': default_year,
    }
    return render(request, 'staff_compensation/payroll_list.html', context)


@login_required(login_url='my-login')
def payroll_detail(request, pk):
    """View individual payroll batch with all employee lines."""
    user = request.user
    is_mgr = _can_manage_compensation(user)

    payroll = get_object_or_404(Payroll.objects.select_related('branch', 'generated_by'), pk=pk)

    if is_mgr:
        lines = payroll.lines.select_related('employee', 'branch').all()
    else:
        lines = payroll.lines.select_related('employee', 'branch').filter(employee=user)
        if not lines.exists():
            return redirect('payroll-list')

    from menu.models import RestaurantSettings
    context = {
        'payroll': payroll,
        'lines': lines,
        'is_manager': is_mgr,
        'currency': RestaurantSettings.load().currency_symbol,
        'base_template': _base_template(user),
    }
    return render(request, 'staff_compensation/payroll_detail.html', context)


@login_required(login_url='my-login')
def payroll_generate(request):
    """Two-step payroll: POST shows preview, confirm POST actually generates."""
    user = request.user
    if not _can_manage_compensation(user):
        messages.error(request, 'You do not have permission to generate payroll.')
        return redirect('compensation-overview')

    now = timezone.localdate()
    if now.month == 1:
        prev_month, prev_year = 12, now.year - 1
    else:
        prev_month, prev_year = now.month - 1, now.year

    if request.method != 'POST':
        return redirect('payroll-list')

    # Step 2: Confirm — actually generate
    if request.POST.get('confirm') == '1':
        try:
            month = int(request.POST.get('month', prev_month))
            year = int(request.POST.get('year', prev_year))
        except (ValueError, TypeError):
            month, year = prev_month, prev_year

        is_global = is_overall_manager(user)
        branch = None if is_global and not request.branch else request.branch

        import datetime
        month_label = datetime.date(year, month, 1).strftime('%B %Y')
        payroll = generate_payroll(year, month, branch=branch, generated_by=user)
        if payroll:
            messages.success(request, f'{month_label} payroll generated — {payroll.employee_count} staff.')
            return redirect('payroll-detail', pk=payroll.pk)
        else:
            messages.info(request, f'{month_label} payroll already generated or no salaried staff.')

        next_url = request.POST.get('next', 'payroll-list')
        if next_url not in ('payroll-list', 'compensation-overview'):
            next_url = 'payroll-list'
        return redirect(next_url)

    # Step 1: Preview
    try:
        month = int(request.POST.get('month', prev_month))
        year = int(request.POST.get('year', prev_year))
        if month < 1 or month > 12 or year < 2024:
            raise ValueError
    except (ValueError, TypeError):
        month, year = prev_month, prev_year

    is_global = is_overall_manager(user)
    branch = None if is_global and not request.branch else request.branch

    # Check if already exists
    if Payroll.objects.filter(month=month, year=year, branch=branch).exists():
        import datetime
        month_label = datetime.date(year, month, 1).strftime('%B %Y')
        messages.info(request, f'{month_label} payroll already generated.')
        return redirect('payroll-list')

    month_label, lines_data = preview_payroll(year, month, branch)

    if not lines_data:
        messages.info(request, f'No salaried staff found for {month_label}.')
        return redirect('payroll-list')

    from decimal import Decimal
    total_basic = sum(d['basic'] for d in lines_data)
    total_commission = sum(d['commission'] for d in lines_data)
    total_gross = sum(d['gross'] for d in lines_data)
    total_advances = sum(d['advance_total'] for d in lines_data)
    total_net = sum(d['net'] for d in lines_data)

    from menu.models import RestaurantSettings
    context = {
        'month_label': month_label,
        'month': month,
        'year': year,
        'lines': lines_data,
        'total_basic': total_basic,
        'total_commission': total_commission,
        'total_gross': total_gross,
        'total_advances': total_advances,
        'total_net': total_net,
        'employee_count': len(lines_data),
        'branch': branch,
        'currency': RestaurantSettings.load().currency_symbol,
        'base_template': _base_template(user),
        'next': request.POST.get('next', 'payroll-list'),
    }
    return render(request, 'staff_compensation/payroll_preview.html', context)


@login_required(login_url='my-login')
def payroll_delete(request, pk):
    """Delete a payroll batch and regenerate."""
    user = request.user
    if not _can_manage_compensation(user):
        messages.error(request, 'Permission denied.')
        return redirect('payroll-list')

    payroll = get_object_or_404(Payroll, pk=pk)
    label = payroll.month_label

    # Un-disburse any advances that were captured
    for line in payroll.lines.all():
        AdvanceRequest.objects.filter(
            employee=line.employee,
            status='disbursed',
        ).update(status='approved')

    payroll.delete()
    messages.success(request, f'{label} payroll deleted.')
    return redirect('payroll-list')


@login_required(login_url='my-login')
def payroll_pdf(request, pk):
    """Generate a payroll summary PDF with all employee lines."""
    import io
    import os

    from django.conf import settings as django_settings
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    user = request.user
    payroll = get_object_or_404(
        Payroll.objects.select_related('branch', 'generated_by'),
        pk=pk,
    )

    if not _can_manage_compensation(user) and not payroll.lines.filter(employee=user).exists():
        return redirect('payroll-list')

    lines = payroll.lines.select_related('employee', 'branch').all()

    from menu.models import RestaurantSettings
    restaurant = RestaurantSettings.load()
    currency = restaurant.currency_symbol
    width, height = A4

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    PRIMARY = colors.HexColor('#1b1f3b')
    ACCENT = colors.HexColor('#7c83ff')
    MUTED = colors.HexColor('#6b7280')
    BORDER = colors.HexColor('#e2e5ea')
    LIGHT_BG = colors.HexColor('#f8f9fb')
    SUCCESS = colors.HexColor('#16a34a')

    table_x = 15 * mm
    table_w = width - 30 * mm
    row_h = 8 * mm

    def draw_header(c, payroll, restaurant, width, height):
        """Draw page header band."""
        c.setFillColor(PRIMARY)
        c.rect(0, height - 26 * mm, width, 26 * mm, fill=True, stroke=False)

        logo_path = None
        if restaurant.logo:
            logo_path = os.path.join(django_settings.MEDIA_ROOT, restaurant.logo.name)
        if not logo_path or not os.path.exists(logo_path):
            logo_path = os.path.join(django_settings.BASE_DIR, 'static', 'icons', 'sanityicon.png')
        if os.path.exists(logo_path):
            c.drawImage(logo_path, 18 * mm, height - 23 * mm, width=20 * mm, height=20 * mm,
                         preserveAspectRatio=True, mask='auto')

        c.setFillColor(colors.white)
        text_center = (40 * mm + (width - 55 * mm)) / 2
        c.setFont('Helvetica-Bold', 16)
        c.drawCentredString(text_center, height - 15 * mm, restaurant.name)
        c.setFont('Helvetica', 8)
        c.drawCentredString(text_center, height - 20 * mm, restaurant.phone or '')

        # Title stamp
        c.setFillColor(colors.HexColor('#e0e7ff'))
        c.roundRect(width - 55 * mm, height - 20 * mm, 40 * mm, 10 * mm, 3, fill=True, stroke=False)
        c.setFillColor(ACCENT)
        c.setFont('Helvetica-Bold', 10)
        c.drawCentredString(width - 35 * mm, height - 17.2 * mm, 'PAYROLL')

    def draw_footer(c, payroll, restaurant, width):
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7)
        c.drawString(15 * mm, 10 * mm, f'Generated on {timezone.localtime().strftime("%d %B %Y at %H:%M")}')
        c.drawRightString(width - 15 * mm, 10 * mm, f'PR-{payroll.pk:04d} · {restaurant.name}')
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.line(15 * mm, 14 * mm, width - 15 * mm, 14 * mm)

    # ── Page 1 ──
    draw_header(c, payroll, restaurant, width, height)

    y = height - 38 * mm

    # Title
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(15 * mm, y, f'Payroll — {payroll.month_label}')
    y -= 6 * mm
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.line(15 * mm, y, 80 * mm, y)
    y -= 8 * mm

    # Meta info
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    c.drawString(15 * mm, y, f'Ref: PR-{payroll.pk:04d}')
    branch_str = payroll.branch.name if payroll.branch else 'All Branches'
    c.drawCentredString(width / 2, y, f'Branch: {branch_str}')
    gen_by = payroll.generated_by.get_full_name() or payroll.generated_by.username if payroll.generated_by else '—'
    c.drawRightString(width - 15 * mm, y, f'Generated by: {gen_by}')
    y -= 6 * mm
    c.drawString(15 * mm, y, f'Employees: {payroll.employee_count}')
    c.drawRightString(width - 15 * mm, y, f'Date: {payroll.generated_at.strftime("%d %B %Y")}')
    y -= 12 * mm

    # ── Summary cards ──
    card_w = (table_w - 8 * mm) / 3
    card_h = 18 * mm
    cards = [
        ('Total Gross', payroll.total_gross, PRIMARY),
        ('Advance Deductions', payroll.total_advances, colors.HexColor('#dc2626')),
        ('Total Net Pay', payroll.total_net, SUCCESS),
    ]
    for i, (label, amount, color) in enumerate(cards):
        cx = table_x + i * (card_w + 4 * mm)
        c.setFillColor(LIGHT_BG)
        c.roundRect(cx, y - card_h, card_w, card_h, 4, fill=True, stroke=False)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.roundRect(cx, y - card_h, card_w, card_h, 4, fill=False, stroke=True)
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7)
        c.drawString(cx + 4 * mm, y - 6 * mm, label.upper())
        c.setFillColor(color)
        c.setFont('Helvetica-Bold', 13)
        c.drawString(cx + 4 * mm, y - 14 * mm, f'{currency} {float(amount):,.2f}')

    y -= card_h + 10 * mm

    # ── Employee table ──
    # Column positions
    col_positions = [
        (table_x + 2 * mm, 'left'),           # #
        (table_x + 10 * mm, 'left'),           # Name
        (table_x + 65 * mm, 'right'),          # Basic
        (table_x + 95 * mm, 'right'),          # Commission
        (table_x + 125 * mm, 'right'),         # Advances
        (table_x + table_w - 2 * mm, 'right'), # Net Pay
    ]
    headers = ['#', 'EMPLOYEE', 'BASIC SALARY', 'COMMISSION', 'ADVANCES', 'NET PAY']

    def draw_table_header():
        nonlocal y
        c.setFillColor(PRIMARY)
        c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 7)
        for idx, (pos, align) in enumerate(col_positions):
            if align == 'right':
                c.drawRightString(pos, y - 5.5 * mm, headers[idx])
            else:
                c.drawString(pos, y - 5.5 * mm, headers[idx])
        y -= row_h

    draw_table_header()

    for i, line in enumerate(lines, 1):
        # Check if we need a new page
        if y < 30 * mm:
            draw_footer(c, payroll, restaurant, width)
            c.showPage()
            draw_header(c, payroll, restaurant, width, height)
            y = height - 38 * mm
            c.setFillColor(PRIMARY)
            c.setFont('Helvetica-Bold', 11)
            c.drawString(15 * mm, y, f'Payroll — {payroll.month_label} (continued)')
            y -= 10 * mm
            draw_table_header()

        # Alternating row bg
        bg = LIGHT_BG if i % 2 == 0 else colors.white
        c.setFillColor(bg)
        c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.3)
        c.line(table_x, y - row_h, table_x + table_w, y - row_h)

        emp_name = line.employee.get_full_name() or line.employee.username
        values = [
            str(i),
            emp_name[:30],
            f'{currency} {float(line.basic_salary):,.2f}',
            f'{currency} {float(line.commission):,.2f}' if line.commission > 0 else '—',
            f'-{currency} {float(line.advance_deductions):,.2f}' if line.advance_deductions > 0 else '—',
            f'{currency} {float(line.net_pay):,.2f}',
        ]

        c.setFillColor(PRIMARY)
        c.setFont('Helvetica', 8)
        for idx, (pos, align) in enumerate(col_positions):
            font = 'Helvetica-Bold' if idx == 5 else 'Helvetica'
            c.setFont(font, 8)
            if idx == 4 and line.advance_deductions > 0:
                c.setFillColor(colors.HexColor('#dc2626'))
            elif idx == 5:
                c.setFillColor(SUCCESS)
            else:
                c.setFillColor(PRIMARY)
            if align == 'right':
                c.drawRightString(pos, y - 5.5 * mm, values[idx])
            else:
                c.drawString(pos, y - 5.5 * mm, values[idx])
        y -= row_h

    # ── Totals row ──
    c.setFillColor(colors.HexColor('#eef2ff'))
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1)
    c.line(table_x, y, table_x + table_w, y)

    c.setFont('Helvetica-Bold', 8)
    c.setFillColor(PRIMARY)
    c.drawString(table_x + 2 * mm, y - 5.5 * mm, '')
    c.drawString(table_x + 10 * mm, y - 5.5 * mm, f'TOTALS ({payroll.employee_count} employees)')
    c.drawRightString(col_positions[2][0], y - 5.5 * mm, f'{currency} {float(payroll.total_basic):,.2f}')
    c.drawRightString(col_positions[3][0], y - 5.5 * mm, f'{currency} {float(payroll.total_commission):,.2f}')
    c.setFillColor(colors.HexColor('#dc2626'))
    adv_str = f'-{currency} {float(payroll.total_advances):,.2f}' if payroll.total_advances > 0 else '—'
    c.drawRightString(col_positions[4][0], y - 5.5 * mm, adv_str)
    c.setFillColor(SUCCESS)
    c.setFont('Helvetica-Bold', 9)
    c.drawRightString(col_positions[5][0], y - 5.5 * mm, f'{currency} {float(payroll.total_net):,.2f}')
    y -= row_h + 16 * mm

    # ── Signature lines ──
    if y > 40 * mm:
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.8)
        c.line(15 * mm, y, 85 * mm, y)
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 8)
        c.drawString(15 * mm, y - 4 * mm, 'Prepared by')
        c.line(115 * mm, y, 195 * mm, y)
        c.drawString(115 * mm, y - 4 * mm, 'Approved by')

    draw_footer(c, payroll, restaurant, width)
    c.showPage()
    c.save()
    buf.seek(0)

    filename = f'payroll_{payroll.month_label.replace(" ", "_")}.pdf'
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required(login_url='my-login')
def payslip_pdf(request, pk):
    """Generate a personal payslip PDF for an individual PayrollLine."""
    import io
    import os

    from django.conf import settings as django_settings
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    user = request.user
    line = get_object_or_404(
        PayrollLine.objects.select_related('payroll', 'payroll__branch', 'employee', 'branch'),
        pk=pk,
    )

    # Staff can only download their own; managers can download any
    if not _can_manage_compensation(user) and line.employee != user:
        return redirect('payroll-list')

    payroll = line.payroll

    from menu.models import RestaurantSettings
    restaurant = RestaurantSettings.load()
    currency = restaurant.currency_symbol
    width, height = A4

    # HR profile
    from hr.models import Employee
    try:
        hr_profile = Employee.objects.select_related('position', 'department').get(user=line.employee)
    except Employee.DoesNotExist:
        hr_profile = None

    emp_user = line.employee
    emp_name = emp_user.get_full_name() or emp_user.username
    emp_id = hr_profile.employee_id if hr_profile else '—'
    position = hr_profile.position.title if hr_profile and hr_profile.position else '—'
    department = hr_profile.department.name if hr_profile and hr_profile.department else '—'
    branch_name = line.branch.name if line.branch else '—'

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    PRIMARY = colors.HexColor('#1b1f3b')
    ACCENT = colors.HexColor('#7c83ff')
    MUTED = colors.HexColor('#6b7280')
    BORDER = colors.HexColor('#e2e5ea')
    LIGHT_BG = colors.HexColor('#f8f9fb')
    SUCCESS = colors.HexColor('#16a34a')

    # ── Header band ──
    c.setFillColor(PRIMARY)
    c.rect(0, height - 26 * mm, width, 26 * mm, fill=True, stroke=False)

    logo_path = None
    if restaurant.logo:
        logo_path = os.path.join(django_settings.MEDIA_ROOT, restaurant.logo.name)
    if not logo_path or not os.path.exists(logo_path):
        logo_path = os.path.join(django_settings.BASE_DIR, 'static', 'icons', 'sanityicon.png')
    if os.path.exists(logo_path):
        c.drawImage(logo_path, 18 * mm, height - 23 * mm, width=20 * mm, height=20 * mm,
                     preserveAspectRatio=True, mask='auto')

    c.setFillColor(colors.white)
    text_center = (40 * mm + (width - 55 * mm)) / 2
    c.setFont('Helvetica-Bold', 16)
    c.drawCentredString(text_center, height - 15 * mm, restaurant.name)
    c.setFont('Helvetica', 8)
    c.drawCentredString(text_center, height - 20 * mm, restaurant.phone or '')

    # Stamp
    c.setFillColor(colors.HexColor('#e0e7ff'))
    c.roundRect(width - 55 * mm, height - 20 * mm, 40 * mm, 10 * mm, 3, fill=True, stroke=False)
    c.setFillColor(ACCENT)
    c.setFont('Helvetica-Bold', 10)
    c.drawCentredString(width - 35 * mm, height - 17.2 * mm, 'PAYSLIP')

    y = height - 38 * mm

    # ── Title ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(20 * mm, y, 'Payslip')
    y -= 6 * mm
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.line(20 * mm, y, 60 * mm, y)
    y -= 10 * mm

    # ── Ref / Period ──
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    c.drawString(20 * mm, y, f'Ref: PS-{line.pk:04d}')
    c.drawRightString(width - 20 * mm, y, f'Period: {payroll.month_label}')
    y -= 12 * mm

    # ── Employee info card ──
    card_h = 38 * mm
    c.setFillColor(LIGHT_BG)
    c.roundRect(20 * mm, y - card_h, width - 40 * mm, card_h, 4, fill=True, stroke=False)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.roundRect(20 * mm, y - card_h, width - 40 * mm, card_h, 4, fill=False, stroke=True)

    cx = 26 * mm
    cy = y - 8 * mm
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(cx, cy, emp_name)
    cy -= 6 * mm
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    c.drawString(cx, cy, f'Employee ID: {emp_id}')
    c.drawString(cx + 70 * mm, cy, f'Branch: {branch_name}')
    cy -= 5 * mm
    c.drawString(cx, cy, f'Position: {position}')
    c.drawString(cx + 70 * mm, cy, f'Department: {department}')

    y -= card_h + 12 * mm

    # ── Earnings table ──
    table_x = 20 * mm
    table_w = width - 40 * mm
    row_h = 9 * mm
    col_desc = table_x + 3 * mm
    col_amt = table_x + table_w - 3 * mm

    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20 * mm, y, 'Earnings')
    y -= 3 * mm

    # Header
    c.setFillColor(PRIMARY)
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(col_desc, y - 6.2 * mm, 'DESCRIPTION')
    c.drawRightString(col_amt, y - 6.2 * mm, 'AMOUNT')
    y -= row_h

    def draw_row(label, amount, bold=False, bg=None):
        nonlocal y
        c.setFillColor(bg or colors.white)
        c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.3)
        c.line(table_x, y - row_h, table_x + table_w, y - row_h)
        c.setFillColor(PRIMARY)
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        c.setFont(font, 10 if bold else 9)
        c.drawString(col_desc, y - 6.2 * mm, label)
        c.drawRightString(col_amt, y - 6.2 * mm, f'{currency} {amount:,.2f}')
        y -= row_h

    draw_row('Basic Salary', float(line.basic_salary))
    if line.commission > 0:
        draw_row('Commission', float(line.commission))
    draw_row('Gross Pay', float(line.gross_pay), bold=True, bg=colors.HexColor('#f0f9ff'))

    y -= 10 * mm

    # ── Deductions table ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20 * mm, y, 'Deductions')
    y -= 3 * mm

    c.setFillColor(colors.HexColor('#7f1d1d'))
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(col_desc, y - 6.2 * mm, 'DESCRIPTION')
    c.drawRightString(col_amt, y - 6.2 * mm, 'AMOUNT')
    y -= row_h

    total_ded = float(line.advance_deductions + line.other_deductions)
    if line.advance_deductions > 0:
        draw_row('Salary Advance Deductions', float(line.advance_deductions))
    if line.other_deductions > 0:
        draw_row('Other Deductions', float(line.other_deductions))
    if total_ded > 0:
        draw_row('Total Deductions', total_ded, bold=True, bg=colors.HexColor('#fef2f2'))
    else:
        draw_row('No Deductions', 0)

    y -= 12 * mm

    # ── Net Pay box ──
    net_box_h = 14 * mm
    c.setFillColor(colors.HexColor('#f0fdf4'))
    c.roundRect(20 * mm, y - net_box_h, table_w, net_box_h, 4, fill=True, stroke=False)
    c.setStrokeColor(SUCCESS)
    c.setLineWidth(1.5)
    c.roundRect(20 * mm, y - net_box_h, table_w, net_box_h, 4, fill=False, stroke=True)

    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(26 * mm, y - 9.5 * mm, 'NET PAY')
    c.setFont('Helvetica-Bold', 16)
    c.setFillColor(SUCCESS)
    c.drawRightString(col_amt, y - 10 * mm, f'{currency} {float(line.net_pay):,.2f}')

    y -= net_box_h + 20 * mm

    # ── Signature lines ──
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.8)
    c.line(20 * mm, y, 90 * mm, y)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawString(20 * mm, y - 4 * mm, 'Authorized Signatory')
    c.line(120 * mm, y, 190 * mm, y)
    c.drawString(120 * mm, y - 4 * mm, 'Employee Signature')

    # ── Footer ──
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 7)
    c.drawString(20 * mm, 12 * mm, f'Generated on {timezone.localtime().strftime("%d %B %Y at %H:%M")}')
    c.drawRightString(width - 20 * mm, 12 * mm, f'PS-{line.pk:04d} · {restaurant.name}')
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(20 * mm, 16 * mm, width - 20 * mm, 16 * mm)

    c.showPage()
    c.save()
    buf.seek(0)

    filename = f'payslip_{emp_name.replace(" ", "_")}_{payroll.month_label.replace(" ", "_")}.pdf'
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
