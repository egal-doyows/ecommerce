import logging

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.models import auth
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.sites.shortcuts import get_current_site
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django_ratelimit.decorators import ratelimit

from .forms import CreateUserForm, LoginForm, UserUpdateForm, WaiterLoginForm, WaiterProfileForm
from .models import WaiterCode
from .token import user_tokenizer_generate
from menu.models import Shift
from staff_compensation.models import StaffCompensation

auth_logger = logging.getLogger('auth')


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


def _ensure_login_shift(user):
    """Create an auto-shift for manager-type roles if one doesn't exist."""
    if not Shift.objects.filter(waiter=user, is_active=True).exists():
        Shift.objects.create(waiter=user, starting_cash=0)


def _get_post_login_redirect(request):
    """Route user to the right landing page after login."""
    user = request.user

    # Superusers → Django admin
    if user.is_superuser:
        return redirect('/restpos/admin/')
    # Owner / Manager → auto-shift + admin dashboard
    if user.groups.filter(name__in=['Owner', 'Manager']).exists():
        _ensure_login_shift(user)
        return redirect('admin-dashboard')
    # Supervisors → auto-shift + POS
    if user.groups.filter(name='Supervisor').exists():
        _ensure_login_shift(user)
        return redirect('pos')
    # Promoters → auto-shift + POS
    if user.groups.filter(name='Promoter').exists():
        _ensure_login_shift(user)
        return redirect('pos')
    # Servers / Cashiers / Kitchen / others → shift or POS
    if Shift.objects.filter(waiter=user, is_active=True).exists():
        return redirect('pos')
    return redirect('shift')


@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def my_login(request):
    form = LoginForm()
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            username = request.POST.get('username')
            password = request.POST.get('password')
            user = authenticate(username=username, password=password)
            if user is None:
                auth_logger.warning('Failed login attempt for username=%s from IP=%s', username, request.META.get('REMOTE_ADDR'))
            if user is not None:
                auth_logger.info('Successful login for user=%s from IP=%s', username, request.META.get('REMOTE_ADDR'))
                # Block attendants from logging in
                if user.groups.filter(name='Attendant').exists():
                    messages.error(request, 'Attendants are not allowed to login. Please contact your manager.')
                    return render(request, 'accounts/my-login.html', {'form': LoginForm()})

                auth.login(request, user)
                # Prompt to create login code if they don't have one
                if not user.is_superuser and not hasattr(user, 'waiter_code'):
                    return redirect('setup-login-code')
                return _get_post_login_redirect(request)

    context = {'form': form}
    return render(request, 'accounts/my-login.html', context)


# Brute-force protection on the 6-digit waiter code. Stack two windows:
#   5/m   — kills typing-fast brute force (humans can't legitimately mistype
#           5 times in 60s; bots try 100s/sec).
#   30/h  — caps sustained attack to 720/day per IP, so even a 100-IP botnet
#           takes ~3 months to walk the full 1M code space.
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
@ratelimit(key='ip', rate='30/h', method='POST', block=True)
def waiter_login(request):
    form = WaiterLoginForm()
    error = None

    if request.method == 'POST':
        form = WaiterLoginForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['code']
            try:
                waiter_code = WaiterCode.objects.get(code=code, is_active=True)
                if not waiter_code.user.is_active:
                    error = 'This account is not active.'
                else:
                    user = waiter_code.user
                    auth.login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    return _get_post_login_redirect(request)
            except WaiterCode.DoesNotExist:
                auth_logger.warning('Failed waiter login attempt with invalid code from IP=%s', request.META.get('REMOTE_ADDR'))
                error = 'Invalid code. Please try again.'

    context = {'form': form, 'error': error}
    return render(request, 'accounts/waiter-login.html', context)


@login_required(login_url='my-login')
def setup_login_code(request):
    # If they already have a code, skip
    if hasattr(request.user, 'waiter_code'):
        return _get_post_login_redirect(request)

    error = None
    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        if len(code) != WaiterCode.CODE_LENGTH or not code.isdigit():
            error = f'Please enter a valid {WaiterCode.CODE_LENGTH}-digit code.'
        elif WaiterCode.objects.filter(code=code).exists():
            error = 'This code is already taken. Choose a different one.'
        else:
            WaiterCode.objects.create(user=request.user, code=code)
            return _get_post_login_redirect(request)

    suggested = WaiterCode.generate_code()
    return render(request, 'accounts/setup-login-code.html', {
        'error': error,
        'suggested': suggested,
    })


@login_required(login_url='my-login')
def dashboard(request):
    from django.utils import timezone
    from django.db.models import Q, Sum, F
    from menu.models import Shift, Order
    user = request.user

    active_shift = Shift.objects.filter(waiter=user, is_active=True).first()
    today_orders = Order.objects.filter(
        Q(waiter=user) | Q(created_by=user),
        created_at__date=timezone.now().date(),
    ).distinct()
    today_sales = today_orders.filter(status='paid').aggregate(
        total=Sum(F('items__unit_price') * F('items__quantity'))
    )['total'] or 0

    return render(request, 'accounts/dashboard.html', {
        'active_shift': active_shift,
        'today_order_count': today_orders.count(),
        'today_sales': today_sales,
    })


def user_logout(request):
    auth.logout(request)
    return redirect('waiter-login')


@login_required(login_url='my-login')
def profile_management(request):
    waiter_code = getattr(request.user, 'waiter_code', None)
    is_staff_user = not (
        request.user.is_superuser
        or request.user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )

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
                    # Only managers can update photo
                    if not is_staff_user and form.cleaned_data.get('photo'):
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
        'is_staff_user': is_staff_user,
    }
    return render(request, 'accounts/profile-management.html', context)


@login_required(login_url='my-login')
def delete_account(request):
    if request.method == 'POST':
        password = request.POST.get('password', '')
        if not request.user.check_password(password):
            messages.error(request, 'Incorrect password. Account was not deactivated.')
            return render(request, 'accounts/delete-account.html', {})
        user = request.user
        auth.logout(request)
        user.is_active = False
        user.save()
        return redirect('my-login')

    return render(request, 'accounts/delete-account.html', {})
