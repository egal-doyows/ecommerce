from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import TaxConfigurationForm
from .models import TaxConfiguration


def _is_admin(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Owner', 'Overall Manager']).exists()
    )


def admin_required(view_func):
    from functools import wraps
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_admin(request.user):
            messages.error(request, 'You do not have permission to manage tax settings.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


@admin_required
def tax_settings(request):
    tax = TaxConfiguration.load()
    if request.method == 'POST':
        form = TaxConfigurationForm(request.POST, instance=tax)
        if form.is_valid():
            form.save()
            messages.success(request, 'Tax settings updated successfully.')
            return redirect('tax-settings')
    else:
        form = TaxConfigurationForm(instance=tax)
    return render(request, 'tax/settings.html', {'form': form, 'tax': tax})
