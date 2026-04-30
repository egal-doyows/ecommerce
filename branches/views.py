from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import (
    is_manager, is_overall_manager,
    full_access_required as superuser_only,
)
from .models import Branch, UserBranch


# ---------------------------------------------------------------------------
# Switch Branch
# ---------------------------------------------------------------------------

def _can_access_all_branches(user):
    return user.is_superuser or user.groups.filter(name__in=['Owner', 'Overall Manager']).exists()


@login_required(login_url='my-login')
def switch_branch(request):
    if request.method == 'POST':
        branch_id = request.POST.get('branch_id', '').strip()

        # "all" → redirect to overall dashboard (cross-branch analytics)
        if branch_id == 'all' and _can_access_all_branches(request.user):
            return redirect('overall-dashboard')

        # Verify user has access to this branch
        if _can_access_all_branches(request.user):
            branch = get_object_or_404(Branch, pk=branch_id, is_active=True)
        else:
            ub = get_object_or_404(
                UserBranch, user=request.user, branch_id=branch_id, branch__is_active=True,
            )
            branch = ub.branch
        request.session['branch_id'] = branch.pk
        messages.success(request, f'Switched to {branch.name}.')

        # Redirect back to where the user was
        next_url = request.POST.get('next', '').strip()
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect('admin-dashboard')

    # GET: redirect to dashboard (switching is now handled inline via the topbar dropdown)
    return redirect('admin-dashboard')


# ---------------------------------------------------------------------------
# Post-login branch selection (Overall Managers)
# ---------------------------------------------------------------------------

@login_required(login_url='my-login')
def post_login_branch_select(request):
    """After login, auto-assign branch from middleware and go to dashboard.
    Overall Managers can switch branches anytime via the topbar dropdown."""
    # Middleware already resolved request.branch — just redirect
    return redirect('admin-dashboard')


# ---------------------------------------------------------------------------
# Branch Management (superuser only)
# ---------------------------------------------------------------------------

@superuser_only
def branch_list(request):
    branches = Branch.objects.annotate(
        staff_count=Count('staff', filter=Q(staff__branch__is_active=True)),
    )
    return render(request, 'branches/branch_list.html', {
        'branches': branches,
    })


@superuser_only
def branch_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().lower()
        if not name or not code:
            messages.error(request, 'Name and code are required.')
            return redirect('branch-create')
        if Branch.objects.filter(code=code).exists():
            messages.error(request, 'A branch with that code already exists.')
            return redirect('branch-create')

        branch = Branch.objects.create(
            name=name,
            code=code,
            address=request.POST.get('address', '').strip(),
            phone=request.POST.get('phone', '').strip(),
            email=request.POST.get('email', '').strip(),
            manager_id=request.POST.get('manager') or None,
        )

        # Auto-assign the creator
        UserBranch.objects.get_or_create(
            user=request.user, branch=branch,
            defaults={'is_primary': False},
        )

        messages.success(request, f'Branch "{name}" created.')
        return redirect('branch-list')

    managers = User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['Branch Manager', 'Overall Manager']),
    ).distinct()

    return render(request, 'branches/branch_form.html', {
        'title': 'Add Branch',
        'action': 'Create',
        'managers': managers,
    })


@superuser_only
def branch_edit(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().lower()
        if not name or not code:
            messages.error(request, 'Name and code are required.')
            return redirect('branch-edit', pk=pk)
        dup = Branch.objects.filter(code=code).exclude(pk=pk)
        if dup.exists():
            messages.error(request, 'A branch with that code already exists.')
            return redirect('branch-edit', pk=pk)

        branch.name = name
        branch.code = code
        branch.address = request.POST.get('address', '').strip()
        branch.phone = request.POST.get('phone', '').strip()
        branch.email = request.POST.get('email', '').strip()
        branch.manager_id = request.POST.get('manager') or None
        branch.is_active = request.POST.get('is_active') == 'on'
        branch.save()

        messages.success(request, f'Branch "{name}" updated.')
        return redirect('branch-list')

    managers = User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['Branch Manager', 'Overall Manager']),
    ).distinct()

    # Staff assigned to this branch
    assigned = UserBranch.objects.filter(branch=branch).select_related('user')

    return render(request, 'branches/branch_form.html', {
        'title': f'Edit {branch.name}',
        'action': 'Save',
        'branch': branch,
        'managers': managers,
        'assigned_staff': assigned,
    })


@superuser_only
def branch_assign_staff(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        user_id = request.POST.get('user')
        is_primary = request.POST.get('is_primary') == 'on'
        if user_id:
            user = get_object_or_404(User, pk=user_id)
            ub, created = UserBranch.objects.get_or_create(
                user=user, branch=branch,
                defaults={'is_primary': is_primary},
            )
            if not created and is_primary != ub.is_primary:
                ub.is_primary = is_primary
                ub.save()
            if is_primary:
                # Unset primary on other branches
                UserBranch.objects.filter(user=user, is_primary=True).exclude(branch=branch).update(is_primary=False)
            messages.success(request, f'{user.username} assigned to {branch.name}.')
    return redirect('branch-edit', pk=pk)


@superuser_only
def branch_remove_staff(request, pk, user_id):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        UserBranch.objects.filter(user_id=user_id, branch=branch).delete()
        messages.success(request, 'Staff removed from branch.')
    return redirect('branch-edit', pk=pk)
