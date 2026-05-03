from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages

from .models import StaffCompensation, StaffBankDetails, PaymentRecord, generate_past_month_records, generate_current_month_record
from .forms import CompensationForm, StaffBankDetailsForm, PaymentDisbursementForm


def _can_manage_compensation(user):
    """Superusers and Managers can manage compensation."""
    if user.is_superuser:
        return True
    return user.groups.filter(name='Manager').exists()


def _get_commission_staff():
    """Return queryset of users in groups that earn commission (Server + Attendant + Promoter)."""
    return User.objects.filter(
        groups__name__in=['Server', 'Attendant', 'Promoter'], is_active=True,
    ).select_related('compensation').prefetch_related('groups')


@login_required(login_url='my-login')
def compensation_overview(request):
    """Manager view: list commission-earning staff (Servers, Attendants & Promoters) this month."""
    if not _can_manage_compensation(request.user):
        return redirect('dashboard')

    staff = _get_commission_staff()

    # Auto-generate payment records for past and current months
    for member in staff:
        generate_past_month_records(member)
        generate_current_month_record(member)

    staff_data = []
    for member in staff:
        comp = getattr(member, 'compensation', None)
        data = {'user': member, 'compensation': comp}
        if comp and comp.compensation_type == 'commission':
            data['current_month_commission'] = comp.get_current_month_commission()
            data['current_month_sales'] = comp.get_current_month_sales()
            data['current_month_eligible_sales'] = comp.get_current_month_eligible_sales()
        # Find the latest pending payment for this staff member
        pending_qs = PaymentRecord.objects.filter(staff=member, status='pending')
        data['pending_payment'] = pending_qs.order_by('-period_end').first()
        from django.db.models import Sum
        data['outstanding'] = pending_qs.aggregate(total=Sum('amount'))['total'] or 0
        staff_data.append(data)

    now = timezone.now()
    context = {
        'staff_data': staff_data,
        'current_month': now.strftime('%B %Y'),
    }
    return render(request, 'staff_compensation/overview.html', context)


@login_required(login_url='my-login')
def staff_detail(request, user_id):
    """Manager view: detailed compensation breakdown for a staff member."""
    if not _can_manage_compensation(request.user):
        return redirect('dashboard')

    staff_user = get_object_or_404(User, pk=user_id)
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
        if compensation.compensation_type == 'commission':
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

    return render(request, 'staff_compensation/detail.html', context)


@login_required(login_url='my-login')
def edit_compensation(request, user_id):
    """Superuser only: edit a staff member's compensation settings."""
    if not request.user.is_superuser:
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
    }
    return render(request, 'staff_compensation/edit.html', context)


@login_required(login_url='my-login')
def payment_list(request):
    """Manager view: payment records for commission-earning staff."""
    if not _can_manage_compensation(request.user):
        return redirect('dashboard')

    # Auto-generate for commission-earning staff
    for user in _get_commission_staff():
        generate_past_month_records(user)
        generate_current_month_record(user)

    front_service_ids = _get_commission_staff().values_list('pk', flat=True)
    payments = PaymentRecord.objects.filter(
        staff_id__in=front_service_ids,
    ).select_related('staff')

    status_filter = request.GET.get('status')
    if status_filter in ('pending', 'paid'):
        payments = payments.filter(status=status_filter)

    context = {
        'payments': payments,
        'status_filter': status_filter,
    }
    return render(request, 'staff_compensation/payments.html', context)


@login_required(login_url='my-login')
def pay_staff(request, pk):
    """Superuser only: pay a pending payment — choose method (cash/bank/mpesa)."""
    if not request.user.is_superuser:
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
    accounts = Account.objects.filter(is_active=True, account_type='cash')
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
    }
    return render(request, 'staff_compensation/pay.html', context)


@login_required(login_url='my-login')
def bank_details_edit(request, user_id):
    """Superuser only: add or edit a staff member's bank details."""
    if not request.user.is_superuser:
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
        if compensation.compensation_type == 'commission':
            context['current_month_commission'] = compensation.get_current_month_commission()
            context['current_month_eligible_sales'] = compensation.get_current_month_eligible_sales()
            start, end = compensation.get_current_month_range()
            context['daily_breakdown'] = compensation.get_daily_breakdown(start, end)

    context['payment_history'] = PaymentRecord.objects.filter(staff=request.user).order_by('-period_end')

    return render(request, 'staff_compensation/my_earnings.html', context)
