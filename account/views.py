from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth.models import auth
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.sites.shortcuts import get_current_site
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .forms import CreateUserForm, LoginForm, UserUpdateForm, WaiterLoginForm, WaiterProfileForm
from .models import WaiterCode
from .token import user_tokenizer_generate
from menu.models import Shift
from staff_compensation.models import StaffCompensation


def register(request):
    form = CreateUserForm()

    if request.method == 'POST':
        form = CreateUserForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.is_active = False
            user.save()

            # Create compensation record
            comp_type = form.cleaned_data['compensation_type']
            scope = form.cleaned_data.get('commission_scope') or 'both'
            StaffCompensation.objects.create(
                user=user,
                compensation_type=comp_type,
                commission_scope=scope,
                commission_rate_regular=form.cleaned_data.get('commission_rate_regular') or 0,
                commission_rate_premium=form.cleaned_data.get('commission_rate_premium') or 0,
                salary_amount=form.cleaned_data.get('salary_amount') or 0,
                payment_frequency=form.cleaned_data.get('payment_frequency') or 'monthly',
            )

            current_site = get_current_site(request)
            subject = 'Account Verification Email'
            message = render_to_string('accounts/registration/email-verification.html', {
                'user': user,
                'domain': current_site,
                'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': user_tokenizer_generate.make_token(user),
            })

            user.email_user(subject=subject, message=message)

            return redirect('email-verification-sent')

    context = {'form': form}
    return render(request, 'accounts/registration/register.html', context)


def email_verification(request, uidb64, token):
    try:
        unique_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=unique_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return redirect('email-verification-failed')

    if user and user_tokenizer_generate.check_token(user, token):
        user.is_active = True
        user.save()
        return redirect('email-verification-success')

    return redirect('email-verification-failed')


def email_verification_sent(request):
    return render(request, 'accounts/registration/email-verification-sent.html')


def email_verification_success(request):
    return render(request, 'accounts/registration/email-verification-success.html')


def email_verification_failed(request):
    return render(request, 'accounts/registration/email-verification-failed.html')


def _get_post_login_redirect(user):
    """Route user to the right landing page after login."""
    # Superusers → Django admin
    if user.is_superuser:
        return redirect('/admin/')
    # Managers → auto-shift (no starting cash) + admin dashboard
    if user.groups.filter(name='Manager').exists():
        if not Shift.objects.filter(waiter=user, is_active=True).exists():
            Shift.objects.create(waiter=user, starting_cash=0)
        return redirect('admin-dashboard')
    # Supervisors → auto-shift + POS (they create orders on behalf of attendants)
    if user.groups.filter(name='Supervisor').exists():
        if not Shift.objects.filter(waiter=user, is_active=True).exists():
            Shift.objects.create(waiter=user, starting_cash=0)
        return redirect('pos')
    # Marketing → auto-shift (no starting cash) + POS
    if user.groups.filter(name='Marketing').exists():
        if not Shift.objects.filter(waiter=user, is_active=True).exists():
            Shift.objects.create(waiter=user, starting_cash=0)
        return redirect('pos')
    # Front Service / Cashiers / others → shift or POS
    if Shift.objects.filter(waiter=user, is_active=True).exists():
        return redirect('pos')
    return redirect('shift')


def my_login(request):
    form = LoginForm()
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            username = request.POST.get('username')
            password = request.POST.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                auth.login(request, user)
                # Prompt to create login code if they don't have one
                if not user.is_superuser and not hasattr(user, 'waiter_code'):
                    return redirect('setup-login-code')
                return _get_post_login_redirect(user)

    context = {'form': form}
    return render(request, 'accounts/my-login.html', context)


def waiter_login(request):
    form = WaiterLoginForm()
    error = None

    if request.method == 'POST':
        form = WaiterLoginForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['code']
            try:
                waiter_code = WaiterCode.objects.get(code=code, is_active=True)
                user = waiter_code.user
                if user.is_active:
                    auth.login(request, user)
                    return _get_post_login_redirect(user)
                else:
                    error = 'This account is not active.'
            except WaiterCode.DoesNotExist:
                error = 'Invalid code. Please try again.'

    context = {'form': form, 'error': error}
    return render(request, 'accounts/waiter-login.html', context)


@login_required(login_url='my-login')
def setup_login_code(request):
    # If they already have a code, skip
    if hasattr(request.user, 'waiter_code'):
        return _get_post_login_redirect(request.user)

    error = None
    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        if len(code) != 6 or not code.isdigit():
            error = 'Please enter a valid 6-digit code.'
        elif WaiterCode.objects.filter(code=code).exists():
            error = 'This code is already taken. Choose a different one.'
        else:
            WaiterCode.objects.create(user=request.user, code=code)
            return _get_post_login_redirect(request.user)

    suggested = WaiterCode.generate_code()
    return render(request, 'accounts/setup-login-code.html', {
        'error': error,
        'suggested': suggested,
    })


@login_required(login_url='my-login')
def dashboard(request):
    from django.utils import timezone
    from menu.models import Shift, Order
    active_shift = Shift.objects.filter(waiter=request.user, is_active=True).first()
    from django.db.models import Q
    today_orders = Order.objects.filter(
        Q(waiter=request.user) | Q(created_by=request.user),
        created_at__date=timezone.now().date(),
    ).distinct()
    context = {
        'active_shift': active_shift,
        'today_order_count': today_orders.count(),
        'today_sales': sum(o.get_total() for o in today_orders.filter(status='paid')),
    }
    return render(request, 'accounts/dashboard.html', context)


def user_logout(request):
    auth.logout(request)
    return redirect('pos')


@login_required(login_url='my-login')
def profile_management(request):
    waiter_code = getattr(request.user, 'waiter_code', None)

    if request.method == 'POST':
        form = WaiterProfileForm(request.POST, request.FILES)
        if form.is_valid():
            if waiter_code:
                new_code = form.cleaned_data['code']
                # Check uniqueness excluding current user
                from .models import WaiterCode
                if WaiterCode.objects.filter(code=new_code).exclude(pk=waiter_code.pk).exists():
                    form.add_error('code', 'This code is already in use.')
                else:
                    waiter_code.code = new_code
                    if form.cleaned_data.get('photo'):
                        waiter_code.photo = form.cleaned_data['photo']
                    waiter_code.save()
                    return redirect('dashboard')
    else:
        initial = {}
        if waiter_code:
            initial['code'] = waiter_code.code
        form = WaiterProfileForm(initial=initial)

    context = {
        'form': form,
        'waiter_code': waiter_code,
    }
    return render(request, 'accounts/profile-management.html', context)


@login_required(login_url='my-login')
def delete_account(request):
    user = User.objects.get(id=request.user.id)

    if request.method == 'POST':
        user.delete()
        return redirect('pos')

    return render(request, 'accounts/delete-account.html', {})
