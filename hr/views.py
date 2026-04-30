from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.permissions import (
    is_manager, is_overall_manager, is_admin_user,
    admin_required as hr_staff_required,
    manager_required as manager_only,
)
from menu.models import RestaurantSettings

from .models import (
    Department, Document, EmergencyContact, Employee,
    LeaveRequest, LeaveType, Position, TransferRequest,
)


def _base_template(user):
    """Return admin base for managers, POS base for regular staff."""
    if is_manager(user):
        return 'administration/base.html'
    return 'menu/base.html'


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@hr_staff_required
def hr_dashboard(request):
    today = timezone.now().date()
    is_overall = is_overall_manager(request.user)

    branch_filter_q = Q(branch=request.branch)
    leave_branch_q = Q(employee__branch=request.branch)

    total_employees = Employee.objects.filter(branch_filter_q, status='active').count()
    on_leave = Employee.objects.filter(branch_filter_q, status='on_leave').count()
    departments = Department.objects.filter(is_active=True).count()
    pending_leaves = LeaveRequest.objects.filter(leave_branch_q, status='pending').count()

    # Recent hires (last 30 days)
    from datetime import timedelta
    recent_hires = Employee.objects.filter(
        branch_filter_q, status='active',
        date_joined__gte=today - timedelta(days=30),
    ).select_related('user', 'department', 'position', 'branch')[:5]

    # Upcoming birthdays (next 30 days)
    upcoming_birthdays = []
    for emp in Employee.objects.filter(branch_filter_q, status='active', date_of_birth__isnull=False).select_related('user'):
        bday_this_year = emp.date_of_birth.replace(year=today.year)
        if bday_this_year < today:
            bday_this_year = bday_this_year.replace(year=today.year + 1)
        diff = (bday_this_year - today).days
        if 0 <= diff <= 30:
            upcoming_birthdays.append({'employee': emp, 'date': bday_this_year, 'days': diff})
    upcoming_birthdays.sort(key=lambda x: x['days'])

    # Department breakdown
    dept_emp_filter = Q(employees__status='active') & Q(employees__branch=request.branch)
    dept_stats = Department.objects.filter(is_active=True).annotate(
        emp_count=Count('employees', filter=dept_emp_filter),
    ).order_by('-emp_count')

    # Employment type breakdown
    type_stats = (
        Employee.objects.filter(branch_filter_q, status='active')
        .values('employment_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # Pending leave requests
    pending_leave_list = LeaveRequest.objects.filter(
        leave_branch_q, status='pending',
    ).select_related('employee__user', 'leave_type')[:5]

    return render(request, 'hr/dashboard.html', {
        'total_employees': total_employees,
        'on_leave': on_leave,
        'departments': departments,
        'pending_leaves': pending_leaves,
        'recent_hires': recent_hires,
        'upcoming_birthdays': upcoming_birthdays[:5],
        'dept_stats': dept_stats,
        'type_stats': type_stats,
        'pending_leave_list': pending_leave_list,
        'is_manager': is_manager(request.user),
        'is_overall': is_overall,
    })


# ---------------------------------------------------------------------------
# Employee List
# ---------------------------------------------------------------------------

@hr_staff_required
def employee_list(request):
    is_overall = is_overall_manager(request.user)
    qs = Employee.objects.filter(branch=request.branch).select_related('user', 'department', 'position', 'branch').prefetch_related('user__groups')

    # Filters
    status_filter = request.GET.get('status', '')
    dept_filter = request.GET.get('department', '')
    type_filter = request.GET.get('type', '')
    search = request.GET.get('q', '')

    if status_filter:
        qs = qs.filter(status=status_filter)
    else:
        qs = qs.exclude(status__in=['terminated', 'resigned'])

    if dept_filter:
        qs = qs.filter(department_id=dept_filter)
    if type_filter:
        qs = qs.filter(employment_type=type_filter)
    if search:
        qs = qs.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__username__icontains=search)
            | Q(employee_id__icontains=search)
            | Q(phone__icontains=search)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'hr/employee_list.html', {
        'employees': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'dept_filter': dept_filter,
        'type_filter': type_filter,
        'search': search,
        'branch_filter': '',
        'branches': [],
        'is_overall': False,
        'departments': Department.objects.filter(is_active=True),
        'status_choices': Employee.STATUS_CHOICES,
        'type_choices': Employee.EMPLOYMENT_TYPE_CHOICES,
        'is_manager': is_manager(request.user),
    })


# ---------------------------------------------------------------------------
# Employee Create
# ---------------------------------------------------------------------------

@manager_only
def employee_create(request):
    if request.method == 'POST':
        # Get user selection
        user_id = request.POST.get('user')
        if not user_id:
            messages.error(request, 'Please select a staff member.')
            return redirect('hr-employee-create')

        user = get_object_or_404(User, pk=user_id)
        if hasattr(user, 'hr_profile'):
            messages.error(request, f'{user.username} already has an HR profile.')
            return redirect('hr-employee-create')

        dept_pk = request.POST.get('department', '')
        pos_pk = request.POST.get('position', '')

        dept = None
        if dept_pk:
            try:
                dept = Department.objects.get(pk=dept_pk)
            except Department.DoesNotExist:
                pass

        pos = None
        if pos_pk:
            try:
                pos = Position.objects.get(pk=pos_pk)
            except Position.DoesNotExist:
                pass

        date_joined_str = request.POST.get('date_joined', '')
        try:
            date_joined = datetime.strptime(date_joined_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            date_joined = timezone.now().date()

        dob_str = request.POST.get('date_of_birth', '')
        dob = None
        if dob_str:
            try:
                dob = datetime.strptime(dob_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass

        # Update user name fields
        user.first_name = request.POST.get('first_name', '').strip()
        user.last_name = request.POST.get('last_name', '').strip()
        user.email = request.POST.get('email', '').strip()
        user.save()

        from branches.utils import resolve_branch
        from branches.models import Branch
        is_overall = is_overall_manager(request.user)

        # Overall managers can pick any branch; others default to their own
        branch = None
        branch_pk = request.POST.get('branch', '')
        if is_overall and branch_pk:
            try:
                branch = Branch.objects.get(pk=branch_pk)
            except Branch.DoesNotExist:
                branch = resolve_branch(request)
        else:
            branch = resolve_branch(request)

        emp = Employee.objects.create(
            user=user,
            branch=branch,
            department=dept,
            position=pos,
            phone=request.POST.get('phone', '').strip(),
            alt_phone=request.POST.get('alt_phone', '').strip(),
            personal_email=request.POST.get('personal_email', '').strip(),
            date_of_birth=dob,
            gender=request.POST.get('gender', ''),
            marital_status=request.POST.get('marital_status', ''),
            national_id=request.POST.get('national_id', '').strip(),
            address=request.POST.get('address', '').strip(),
            employment_type=request.POST.get('employment_type', 'full_time'),
            date_joined=date_joined,
            notes=request.POST.get('notes', '').strip(),
        )

        messages.success(request, f'Employee profile created for {emp.full_name} ({emp.employee_id}).')
        return redirect('hr-employee-detail', pk=emp.pk)

    # Users without HR profiles
    existing_ids = Employee.objects.values_list('user_id', flat=True)
    available_users = User.objects.filter(
        is_superuser=False,
    ).exclude(pk__in=existing_ids).order_by('first_name', 'username')

    from branches.models import Branch
    is_overall = is_overall_manager(request.user)

    return render(request, 'hr/employee_form.html', {
        'title': 'Create Employee Profile',
        'available_users': available_users,
        'departments': Department.objects.filter(is_active=True),
        'positions': Position.objects.filter(is_active=True),
        'branches': Branch.objects.filter(is_active=True).order_by('name') if is_overall else [],
        'gender_choices': Employee.GENDER_CHOICES,
        'marital_choices': Employee.MARITAL_CHOICES,
        'type_choices': Employee.EMPLOYMENT_TYPE_CHOICES,
        'today': timezone.now().date().isoformat(),
        'is_create': True,
        'is_overall': is_overall,
    })


# ---------------------------------------------------------------------------
# Employee Detail
# ---------------------------------------------------------------------------

@hr_staff_required
def employee_detail(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    emp = get_object_or_404(
        Employee.objects.select_related('user', 'department', 'position', 'branch'),
        **filter_kwargs,
    )
    contacts = emp.emergency_contacts.all()
    documents = emp.documents.select_related('uploaded_by').all()
    leaves = emp.leave_requests.select_related('leave_type', 'reviewed_by')[:10]

    # Leave balance for current year
    current_year = timezone.now().year
    leave_summary = []
    for lt in LeaveType.objects.filter(is_active=True):
        used = LeaveRequest.objects.filter(
            employee=emp,
            leave_type=lt,
            status='approved',
            start_date__year=current_year,
        ).aggregate(
            total=Sum(models.F('end_date') - models.F('start_date'))
        )['total']
        used_days = used.days + 1 if used else 0
        # Count individual requests to add the +1 per request
        approved_count = LeaveRequest.objects.filter(
            employee=emp, leave_type=lt, status='approved', start_date__year=current_year,
        ).count()
        if approved_count > 0:
            total_used = sum(
                lr.days for lr in LeaveRequest.objects.filter(
                    employee=emp, leave_type=lt, status='approved', start_date__year=current_year,
                )
            )
        else:
            total_used = 0
        leave_summary.append({
            'type': lt,
            'allowed': lt.days_allowed,
            'used': total_used,
            'remaining': max(lt.days_allowed - total_used, 0) if lt.days_allowed > 0 else None,
        })

    from branches.models import Branch
    return render(request, 'hr/employee_detail.html', {
        'emp': emp,
        'contacts': contacts,
        'documents': documents,
        'leaves': leaves,
        'leave_summary': leave_summary,
        'is_manager': is_manager(request.user),
        'doc_categories': Document.CATEGORY_CHOICES,
        'departments': Department.objects.filter(is_active=True),
        'positions': Position.objects.filter(is_active=True).select_related('department'),
        'branches': Branch.objects.filter(is_active=True).order_by('name'),
    })


# ---------------------------------------------------------------------------
# Employee Edit
# ---------------------------------------------------------------------------

@manager_only
def employee_edit(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    emp = get_object_or_404(Employee.objects.select_related('user', 'branch'), **filter_kwargs)

    if request.method == 'POST':
        emp.user.first_name = request.POST.get('first_name', '').strip()
        emp.user.last_name = request.POST.get('last_name', '').strip()
        emp.user.email = request.POST.get('email', '').strip()
        emp.user.save()

        # Branch (overall managers only)
        if is_overall:
            branch_pk = request.POST.get('branch', '')
            if branch_pk:
                from branches.models import Branch
                try:
                    emp.branch = Branch.objects.get(pk=branch_pk)
                except Branch.DoesNotExist:
                    pass
            else:
                emp.branch = None

        dept_pk = request.POST.get('department', '')
        pos_pk = request.POST.get('position', '')

        emp.department = None
        if dept_pk:
            try:
                emp.department = Department.objects.get(pk=dept_pk)
            except Department.DoesNotExist:
                pass

        emp.position = None
        if pos_pk:
            try:
                emp.position = Position.objects.get(pk=pos_pk)
            except Position.DoesNotExist:
                pass

        emp.phone = request.POST.get('phone', '').strip()
        emp.alt_phone = request.POST.get('alt_phone', '').strip()
        emp.personal_email = request.POST.get('personal_email', '').strip()
        emp.gender = request.POST.get('gender', '')
        emp.marital_status = request.POST.get('marital_status', '')
        emp.national_id = request.POST.get('national_id', '').strip()
        emp.address = request.POST.get('address', '').strip()
        emp.employment_type = request.POST.get('employment_type', emp.employment_type)
        emp.notes = request.POST.get('notes', '').strip()

        dob_str = request.POST.get('date_of_birth', '')
        if dob_str:
            try:
                emp.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass
        else:
            emp.date_of_birth = None

        dj_str = request.POST.get('date_joined', '')
        if dj_str:
            try:
                emp.date_joined = datetime.strptime(dj_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass

        emp.save()
        messages.success(request, f'{emp.full_name} profile updated.')
        return redirect('hr-employee-detail', pk=emp.pk)

    from branches.models import Branch
    return render(request, 'hr/employee_form.html', {
        'title': f'Edit {emp.full_name}',
        'emp': emp,
        'departments': Department.objects.filter(is_active=True),
        'positions': Position.objects.filter(is_active=True),
        'branches': Branch.objects.filter(is_active=True).order_by('name'),
        'gender_choices': Employee.GENDER_CHOICES,
        'marital_choices': Employee.MARITAL_CHOICES,
        'type_choices': Employee.EMPLOYMENT_TYPE_CHOICES,
        'is_create': False,
        'is_overall': is_overall,
    })


# ---------------------------------------------------------------------------
# Employee Status Change
# ---------------------------------------------------------------------------

@manager_only
def employee_status(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    emp = get_object_or_404(Employee, **filter_kwargs)
    if request.method == 'POST':
        new_status = request.POST.get('status', emp.status)
        if new_status in dict(Employee.STATUS_CHOICES):
            old = emp.get_status_display()
            emp.status = new_status
            if new_status in ('terminated', 'resigned') and not emp.date_left:
                emp.date_left = timezone.now().date()
            elif new_status == 'active':
                emp.date_left = None
            emp.save()
            messages.success(request, f'{emp.full_name} status changed from {old} to {emp.get_status_display()}.')
    return redirect('hr-employee-detail', pk=pk)


# ---------------------------------------------------------------------------
# Employee Transfer (Department / Position)
# ---------------------------------------------------------------------------

@manager_only
def employee_transfer(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    emp = get_object_or_404(Employee.objects.select_related('department', 'position', 'branch'), **filter_kwargs)
    if request.method == 'POST':
        from branches.models import Branch

        branch_pk = request.POST.get('branch', '')
        dept_pk = request.POST.get('department', '')
        pos_pk = request.POST.get('position', '')
        note = request.POST.get('transfer_note', '').strip()

        # Resolve target branch
        target_branch = emp.branch
        if branch_pk:
            try:
                target_branch = Branch.objects.get(pk=branch_pk)
            except Branch.DoesNotExist:
                target_branch = emp.branch

        branch_is_changing = target_branch and emp.branch and target_branch.pk != emp.branch.pk

        # Resolve dept/position
        new_dept = None
        if dept_pk:
            try:
                new_dept = Department.objects.get(pk=dept_pk)
            except Department.DoesNotExist:
                pass

        new_pos = None
        if pos_pk:
            try:
                new_pos = Position.objects.get(pk=pos_pk)
            except Position.DoesNotExist:
                pass

        if branch_is_changing and not is_overall:
            # Branch Manager: create a transfer request for approval
            if TransferRequest.objects.filter(employee=emp, status='pending').exists():
                messages.error(request, f'{emp.full_name} already has a pending transfer request.')
                return redirect('hr-employee-detail', pk=pk)

            TransferRequest.objects.create(
                employee=emp,
                requested_by=request.user,
                from_branch=emp.branch,
                to_branch=target_branch,
                new_department=new_dept,
                new_position=new_pos,
                note=note,
            )
            messages.success(request, f'Transfer request submitted for {emp.full_name} → {target_branch.name}. Awaiting approval.')
        else:
            # Overall Manager: execute transfer directly
            # Or same-branch dept/position change for any manager
            old_branch = emp.branch.name if emp.branch else 'None'
            old_dept = emp.department.name if emp.department else 'None'
            old_pos = emp.position.title if emp.position else 'None'

            if branch_is_changing:
                emp.branch = target_branch
            emp.department = new_dept
            emp.position = new_pos

            new_branch_name = emp.branch.name if emp.branch else 'None'
            new_dept_name = emp.department.name if emp.department else 'None'
            new_pos_name = emp.position.title if emp.position else 'None'

            changed = old_dept != new_dept_name or old_pos != new_pos_name or old_branch != new_branch_name
            if note or changed:
                transfer_log = f'[{timezone.now().date()}] Transferred: {old_branch}/{old_dept}/{old_pos} → {new_branch_name}/{new_dept_name}/{new_pos_name}'
                if note:
                    transfer_log += f' — {note}'
                emp.notes = f'{transfer_log}\n{emp.notes}'.strip() if emp.notes else transfer_log

            emp.save()

            # Update UserBranch if branch changed
            if emp.user and emp.branch and old_branch != new_branch_name:
                from branches.models import UserBranch
                UserBranch.objects.filter(user=emp.user).delete()
                UserBranch.objects.create(user=emp.user, branch=emp.branch, is_primary=True)

            messages.success(request, f'{emp.full_name} transferred to {new_branch_name} / {new_dept_name} / {new_pos_name}.')
    return redirect('hr-employee-detail', pk=pk)


# ---------------------------------------------------------------------------
# Transfer Requests (approval workflow)
# ---------------------------------------------------------------------------

@manager_only
def transfer_request_list(request):
    if not is_overall_manager(request.user):
        messages.error(request, 'Only Overall Managers can manage transfer requests.')
        return redirect('hr-dashboard')

    status_filter = request.GET.get('status', 'pending')
    qs = TransferRequest.objects.select_related(
        'employee__user', 'from_branch', 'to_branch',
        'new_department', 'new_position', 'requested_by', 'reviewed_by',
    )
    if status_filter in ('pending', 'approved', 'rejected'):
        qs = qs.filter(status=status_filter)

    return render(request, 'hr/transfer_list.html', {
        'transfers': qs,
        'status_filter': status_filter,
    })


@manager_only
def transfer_request_approve(request, pk):
    if not is_overall_manager(request.user):
        messages.error(request, 'Only Overall Managers can approve transfers.')
        return redirect('hr-dashboard')

    tr = get_object_or_404(TransferRequest, pk=pk, status='pending')
    if request.method == 'POST':
        emp = tr.employee

        # Apply the transfer
        old_branch = emp.branch.name if emp.branch else 'None'
        emp.branch = tr.to_branch
        if tr.new_department:
            emp.department = tr.new_department
        if tr.new_position:
            emp.position = tr.new_position

        new_branch = emp.branch.name if emp.branch else 'None'
        transfer_log = f'[{timezone.now().date()}] Transfer approved: {old_branch} → {new_branch}'
        review_note = request.POST.get('note', '').strip()
        if review_note:
            transfer_log += f' — {review_note}'
        emp.notes = f'{transfer_log}\n{emp.notes}'.strip() if emp.notes else transfer_log
        emp.save()

        # Update UserBranch
        if emp.user and emp.branch:
            from branches.models import UserBranch
            UserBranch.objects.filter(user=emp.user).delete()
            UserBranch.objects.create(user=emp.user, branch=emp.branch, is_primary=True)

        # Mark as approved
        tr.status = 'approved'
        tr.reviewed_by = request.user
        tr.review_note = review_note
        tr.reviewed_at = timezone.now()
        tr.save()

        messages.success(request, f'Transfer approved — {emp.full_name} moved to {new_branch}.')
    return redirect('hr-transfer-list')


@manager_only
def transfer_request_reject(request, pk):
    if not is_overall_manager(request.user):
        messages.error(request, 'Only Overall Managers can reject transfers.')
        return redirect('hr-dashboard')

    tr = get_object_or_404(TransferRequest, pk=pk, status='pending')
    if request.method == 'POST':
        tr.status = 'rejected'
        tr.reviewed_by = request.user
        tr.review_note = request.POST.get('note', '').strip()
        tr.reviewed_at = timezone.now()
        tr.save()

        messages.success(request, f'Transfer request for {tr.employee.full_name} rejected.')
    return redirect('hr-transfer-list')


# ---------------------------------------------------------------------------
# Emergency Contacts
# ---------------------------------------------------------------------------

@manager_only
def emergency_contact_add(request, emp_pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': emp_pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    emp = get_object_or_404(Employee, **filter_kwargs)
    if request.method == 'POST':
        EmergencyContact.objects.create(
            employee=emp,
            name=request.POST.get('name', '').strip(),
            relationship=request.POST.get('relationship', 'other'),
            phone=request.POST.get('phone', '').strip(),
            alt_phone=request.POST.get('alt_phone', '').strip(),
            address=request.POST.get('address', '').strip(),
        )
        messages.success(request, 'Emergency contact added.')
    return redirect('hr-employee-detail', pk=emp_pk)


@manager_only
def emergency_contact_edit(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['employee__branch'] = request.branch
    contact = get_object_or_404(EmergencyContact, **filter_kwargs)
    if request.method == 'POST':
        contact.name = request.POST.get('name', '').strip()
        contact.relationship = request.POST.get('relationship', contact.relationship)
        contact.phone = request.POST.get('phone', '').strip()
        contact.alt_phone = request.POST.get('alt_phone', '').strip()
        contact.address = request.POST.get('address', '').strip()
        contact.save()
        messages.success(request, 'Emergency contact updated.')
    return redirect('hr-employee-detail', pk=contact.employee_id)


@manager_only
def emergency_contact_delete(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['employee__branch'] = request.branch
    contact = get_object_or_404(EmergencyContact, **filter_kwargs)
    emp_pk = contact.employee_id
    if request.method == 'POST':
        contact.delete()
        messages.success(request, 'Emergency contact removed.')
    return redirect('hr-employee-detail', pk=emp_pk)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@manager_only
def document_upload(request, emp_pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': emp_pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    emp = get_object_or_404(Employee, **filter_kwargs)
    if request.method == 'POST' and request.FILES.get('file'):
        uploaded_file = request.FILES['file']

        # Validate file before saving
        from core.models import validate_file_size, validate_file_extension
        from django.core.exceptions import ValidationError
        try:
            validate_file_size(uploaded_file)
            validate_file_extension(uploaded_file)
        except ValidationError as e:
            messages.error(request, e.message)
            return redirect('hr-employee-detail', pk=emp_pk)

        # Sanitize filename
        import os
        from django.utils.text import get_valid_filename
        uploaded_file.name = get_valid_filename(uploaded_file.name)

        Document.objects.create(
            employee=emp,
            title=request.POST.get('title', '').strip() or uploaded_file.name,
            category=request.POST.get('category', 'other'),
            file=uploaded_file,
            notes=request.POST.get('notes', '').strip(),
            uploaded_by=request.user,
        )
        messages.success(request, 'Document uploaded.')
    return redirect('hr-employee-detail', pk=emp_pk)


@manager_only
def document_delete(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['employee__branch'] = request.branch
    doc = get_object_or_404(Document, **filter_kwargs)
    emp_pk = doc.employee_id
    if request.method == 'POST':
        doc.file.delete(save=False)
        doc.delete()
        messages.success(request, 'Document deleted.')
    return redirect('hr-employee-detail', pk=emp_pk)


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------

@manager_only
def department_list(request):
    depts = Department.objects.annotate(
        emp_count=Count('employees', filter=Q(employees__status='active')),
        position_count=Count('positions', filter=Q(positions__is_active=True)),
    )
    return render(request, 'hr/department_list.html', {
        'departments': depts,
    })


@manager_only
def department_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Department name is required.')
            return redirect('hr-department-create')
        if Department.objects.filter(name__iexact=name).exists():
            messages.error(request, 'A department with that name already exists.')
            return redirect('hr-department-create')
        Department.objects.create(
            name=name,
            description=request.POST.get('description', '').strip(),
            head_id=request.POST.get('head') or None,
        )
        messages.success(request, f'Department "{name}" created.')
        return redirect('hr-department-list')

    managers = User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['Branch Manager', 'Overall Manager']),
    ).distinct()

    return render(request, 'hr/department_form.html', {
        'title': 'Add Department',
        'action': 'Create',
        'managers': managers,
    })


@manager_only
def department_edit(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Department name is required.')
            return redirect('hr-department-edit', pk=pk)
        dup = Department.objects.filter(name__iexact=name).exclude(pk=pk)
        if dup.exists():
            messages.error(request, 'A department with that name already exists.')
            return redirect('hr-department-edit', pk=pk)
        dept.name = name
        dept.description = request.POST.get('description', '').strip()
        dept.head_id = request.POST.get('head') or None
        dept.is_active = request.POST.get('is_active') == 'on'
        dept.save()
        messages.success(request, f'Department "{name}" updated.')
        return redirect('hr-department-list')

    managers = User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['Branch Manager', 'Overall Manager']),
    ).distinct()

    return render(request, 'hr/department_form.html', {
        'title': f'Edit {dept.name}',
        'action': 'Save',
        'dept': dept,
        'managers': managers,
    })


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@manager_only
def position_list(request):
    positions = Position.objects.select_related('department').annotate(
        emp_count=Count('employees', filter=Q(employees__status='active')),
    )
    return render(request, 'hr/position_list.html', {
        'positions': positions,
    })


@manager_only
def position_create(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        if not title:
            messages.error(request, 'Position title is required.')
            return redirect('hr-position-create')
        if Position.objects.filter(title__iexact=title).exists():
            messages.error(request, 'A position with that title already exists.')
            return redirect('hr-position-create')
        dept_pk = request.POST.get('department', '')
        dept = None
        if dept_pk:
            try:
                dept = Department.objects.get(pk=dept_pk)
            except Department.DoesNotExist:
                pass
        Position.objects.create(
            title=title,
            department=dept,
            description=request.POST.get('description', '').strip(),
        )
        messages.success(request, f'Position "{title}" created.')
        return redirect('hr-position-list')

    return render(request, 'hr/position_form.html', {
        'title_text': 'Add Position',
        'action': 'Create',
        'departments': Department.objects.filter(is_active=True),
    })


@manager_only
def position_edit(request, pk):
    pos = get_object_or_404(Position, pk=pk)
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        if not title:
            messages.error(request, 'Position title is required.')
            return redirect('hr-position-edit', pk=pk)
        dup = Position.objects.filter(title__iexact=title).exclude(pk=pk)
        if dup.exists():
            messages.error(request, 'A position with that title already exists.')
            return redirect('hr-position-edit', pk=pk)
        dept_pk = request.POST.get('department', '')
        pos.title = title
        pos.department = None
        if dept_pk:
            try:
                pos.department = Department.objects.get(pk=dept_pk)
            except Department.DoesNotExist:
                pass
        pos.description = request.POST.get('description', '').strip()
        pos.is_active = request.POST.get('is_active') == 'on'
        pos.save()
        messages.success(request, f'Position "{title}" updated.')
        return redirect('hr-position-list')

    return render(request, 'hr/position_form.html', {
        'title_text': f'Edit {pos.title}',
        'action': 'Save',
        'pos': pos,
        'departments': Department.objects.filter(is_active=True),
    })


# ---------------------------------------------------------------------------
# Leave Management
# ---------------------------------------------------------------------------

@login_required(login_url='my-login')
def leave_list(request):
    is_manager = is_manager(request.user)
    if is_manager:
        qs = LeaveRequest.objects.all()
        branch = getattr(request, 'branch', None)
        if branch and not is_overall_manager(request.user):
            qs = qs.filter(employee__branch=branch)
    else:
        # Regular staff: only their own leaves
        qs = LeaveRequest.objects.filter(employee__user=request.user)

    qs = qs.select_related(
        'employee__user', 'employee__branch', 'leave_type', 'reviewed_by',
    )

    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    search = request.GET.get('q', '')

    if status_filter:
        qs = qs.filter(status=status_filter)
    elif is_manager:
        # Hide cancelled requests by default for managers
        qs = qs.exclude(status='cancelled')
    if type_filter:
        qs = qs.filter(leave_type_id=type_filter)
    if search and is_manager:
        qs = qs.filter(
            Q(employee__user__first_name__icontains=search)
            | Q(employee__user__last_name__icontains=search)
            | Q(employee__user__username__icontains=search)
        )

    pending_count = qs.filter(status='pending').count()

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'hr/leave_list.html', {
        'leaves': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'search': search,
        'pending_count': pending_count,
        'status_choices': LeaveRequest.STATUS_CHOICES,
        'leave_types': LeaveType.objects.filter(is_active=True),
        'is_manager': is_manager,
        'base_template': _base_template(request.user),
    })


@login_required(login_url='my-login')
def leave_request(request):
    if request.method == 'POST':
        # Determine the employee
        emp = None
        if is_manager(request.user):
            emp_pk = request.POST.get('employee', '')
            if emp_pk:
                emp = Employee.objects.filter(pk=emp_pk).first()
        if not emp:
            emp = getattr(request.user, 'hr_profile', None)
        if not emp:
            messages.error(request, 'No employee profile found.')
            return redirect('hr-leave-list')

        lt_pk = request.POST.get('leave_type', '')
        start_str = request.POST.get('start_date', '')
        end_str = request.POST.get('end_date', '')

        try:
            start = datetime.strptime(start_str, '%Y-%m-%d').date()
            end = datetime.strptime(end_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            messages.error(request, 'Invalid dates.')
            return redirect('hr-leave-request')

        if end < start:
            messages.error(request, 'End date cannot be before start date.')
            return redirect('hr-leave-request')

        lt = get_object_or_404(LeaveType, pk=lt_pk)

        lr = LeaveRequest.objects.create(
            employee=emp,
            leave_type=lt,
            start_date=start,
            end_date=end,
            reason=request.POST.get('reason', '').strip(),
        )
        messages.success(request, f'Leave request submitted ({lr.days} day{"s" if lr.days != 1 else ""}).')
        return redirect('hr-leave-detail', pk=lr.pk)

    employees = Employee.objects.filter(status='active', branch=request.branch).select_related('user') if is_manager(request.user) else None

    return render(request, 'hr/leave_form.html', {
        'title': 'Request Leave',
        'leave_types': LeaveType.objects.filter(is_active=True),
        'employees': employees,
        'is_manager': is_manager(request.user),
        'today': timezone.now().date().isoformat(),
        'base_template': _base_template(request.user),
    })


@login_required(login_url='my-login')
def leave_detail(request, pk):
    is_mgr = is_manager(request.user)
    is_overall = is_overall_manager(request.user)

    if is_mgr:
        filter_kwargs = {'pk': pk}
        if not is_overall:
            filter_kwargs['employee__branch'] = request.branch
    else:
        # Regular staff can only view their own leave requests
        filter_kwargs = {'pk': pk, 'employee__user': request.user}

    lr = get_object_or_404(
        LeaveRequest.objects.select_related(
            'employee__user', 'employee__department', 'employee__branch', 'leave_type', 'reviewed_by',
        ),
        **filter_kwargs,
    )
    return render(request, 'hr/leave_detail.html', {
        'lr': lr,
        'is_manager': is_mgr,
        'base_template': _base_template(request.user),
    })


@manager_only
def leave_approve(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['employee__branch'] = request.branch
    lr = get_object_or_404(LeaveRequest, **filter_kwargs)
    if request.method == 'POST' and lr.status == 'pending':
        lr.status = 'approved'
        lr.reviewed_by = request.user
        lr.review_note = request.POST.get('note', '').strip()
        lr.reviewed_at = timezone.now()
        lr.save()
        messages.success(request, f'Leave approved for {lr.employee.full_name}.')
    return redirect('hr-leave-detail', pk=pk)


@manager_only
def leave_reject(request, pk):
    is_overall = is_overall_manager(request.user)
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['employee__branch'] = request.branch
    lr = get_object_or_404(LeaveRequest, **filter_kwargs)
    if request.method == 'POST' and lr.status == 'pending':
        lr.status = 'rejected'
        lr.reviewed_by = request.user
        lr.review_note = request.POST.get('note', '').strip()
        lr.reviewed_at = timezone.now()
        lr.save()
        messages.success(request, f'Leave rejected for {lr.employee.full_name}.')
    return redirect('hr-leave-detail', pk=pk)


@login_required(login_url='my-login')
def leave_cancel(request, pk):
    lr = get_object_or_404(LeaveRequest, pk=pk)
    is_own = hasattr(request.user, 'hr_profile') and lr.employee == request.user.hr_profile
    if not is_own:
        messages.error(request, 'Only the requester can cancel a leave request.')
        return redirect('hr-leave-detail', pk=pk)
    if request.method == 'POST' and lr.status == 'pending':
        lr.status = 'cancelled'
        lr.save()
        messages.success(request, 'Leave request cancelled.')
    return redirect('hr-leave-detail', pk=pk)


@login_required(login_url='my-login')
def leave_pdf(request, pk):
    """Generate a professional PDF letter for an approved leave request."""
    import io
    import os

    from django.conf import settings as django_settings
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    is_mgr = is_manager(request.user)
    if is_mgr:
        filter_kwargs = {'pk': pk, 'status': 'approved'}
    else:
        filter_kwargs = {'pk': pk, 'status': 'approved', 'employee__user': request.user}

    lr = get_object_or_404(
        LeaveRequest.objects.select_related(
            'employee__user', 'employee__department', 'employee__position',
            'employee__branch', 'leave_type', 'reviewed_by',
        ),
        **filter_kwargs,
    )

    restaurant = RestaurantSettings.load()
    emp = lr.employee
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

    y = height - 30 * mm  # starting y

    # ── Header band ──
    c.setFillColor(PRIMARY)
    c.rect(0, height - 26 * mm, width, 26 * mm, fill=True, stroke=False)

    # Logo
    logo_path = None
    if restaurant.logo:
        logo_path = os.path.join(django_settings.MEDIA_ROOT, restaurant.logo.name)
    if not logo_path or not os.path.exists(logo_path):
        logo_path = os.path.join(django_settings.BASE_DIR, 'static', 'icons', 'sanityicon.png')
    if os.path.exists(logo_path):
        c.drawImage(logo_path, 18 * mm, height - 23 * mm, width=20 * mm, height=20 * mm,
                     preserveAspectRatio=True, mask='auto')

    # Company name
    c.setFillColor(colors.white)
    text_center = (40 * mm + (width - 55 * mm)) / 2
    c.setFont('Helvetica-Bold', 16)
    c.drawCentredString(text_center, height - 15 * mm, restaurant.name)
    c.setFont('Helvetica', 8)
    c.drawCentredString(text_center, height - 20 * mm, restaurant.phone or '')

    # "APPROVED" stamp
    c.setFillColor(colors.HexColor('#dcfce7'))
    c.roundRect(width - 55 * mm, height - 20 * mm, 40 * mm, 10 * mm, 3, fill=True, stroke=False)
    c.setFillColor(SUCCESS)
    c.setFont('Helvetica-Bold', 10)
    c.drawCentredString(width - 35 * mm, height - 17.2 * mm, 'APPROVED')

    y = height - 38 * mm

    # ── Title ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(20 * mm, y, 'Leave Approval Letter')
    y -= 6 * mm
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.line(20 * mm, y, 80 * mm, y)
    y -= 10 * mm

    # ── Reference / Date ──
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    ref = f'Ref: LV-{lr.pk:04d}'
    c.drawString(20 * mm, y, ref)
    date_str = lr.reviewed_at.strftime('%d %B %Y') if lr.reviewed_at else ''
    c.drawRightString(width - 20 * mm, y, f'Date: {date_str}')
    y -= 12 * mm

    # ── Employee info card ──
    card_h = 38 * mm
    c.setFillColor(LIGHT_BG)
    c.roundRect(20 * mm, y - card_h, width - 40 * mm, card_h, 4, fill=True, stroke=False)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.roundRect(20 * mm, y - card_h, width - 40 * mm, card_h, 4, fill=False, stroke=True)

    # Employee details inside card
    cx = 26 * mm
    cy = y - 8 * mm
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(cx, cy, emp.full_name)
    cy -= 6 * mm
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    c.drawString(cx, cy, f'Employee ID: {emp.employee_id}')
    cy -= 5 * mm
    if emp.position:
        c.drawString(cx, cy, f'Position: {emp.position.title}')
        cy -= 5 * mm
    if emp.department:
        c.drawString(cx, cy, f'Department: {emp.department.name}')
        cy -= 5 * mm
    if emp.branch:
        c.drawString(cx, cy, f'Branch: {emp.branch.name}')

    y -= card_h + 12 * mm

    # ── Leave details table ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20 * mm, y, 'Leave Details')
    y -= 3 * mm

    # Table header
    table_x = 20 * mm
    table_w = width - 40 * mm
    row_h = 9 * mm

    c.setFillColor(PRIMARY)
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 8)
    cols = [table_x + 3 * mm, table_x + 45 * mm, table_x + 90 * mm, table_x + 120 * mm]
    c.drawString(cols[0], y - 6.2 * mm, 'LEAVE TYPE')
    c.drawString(cols[1], y - 6.2 * mm, 'START DATE')
    c.drawString(cols[2], y - 6.2 * mm, 'END DATE')
    c.drawString(cols[3], y - 6.2 * mm, 'DAYS')
    y -= row_h

    # Table row
    c.setFillColor(colors.white)
    c.rect(table_x, y - row_h, table_w, row_h, fill=True, stroke=False)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.rect(table_x, y - row_h, table_w, row_h, fill=False, stroke=True)

    c.setFillColor(PRIMARY)
    c.setFont('Helvetica', 9)
    paid_tag = ' (Paid)' if lr.leave_type.is_paid else ' (Unpaid)'
    c.drawString(cols[0], y - 6.2 * mm, f'{lr.leave_type.name}{paid_tag}')
    c.drawString(cols[1], y - 6.2 * mm, lr.start_date.strftime('%d %b %Y'))
    c.drawString(cols[2], y - 6.2 * mm, lr.end_date.strftime('%d %b %Y'))
    c.setFont('Helvetica-Bold', 10)
    c.drawString(cols[3], y - 6.2 * mm, str(lr.days))
    y -= row_h + 10 * mm

    # ── Reason ──
    if lr.reason:
        c.setFillColor(PRIMARY)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(20 * mm, y, 'Reason for Leave')
        y -= 6 * mm
        c.setFont('Helvetica', 9)
        c.setFillColor(MUTED)
        # Word-wrap reason text
        from reportlab.lib.utils import simpleSplit
        lines = simpleSplit(lr.reason, 'Helvetica', 9, table_w)
        for line in lines[:6]:
            c.drawString(20 * mm, y, line)
            y -= 4.5 * mm
        y -= 6 * mm

    # ── Approval info ──
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(20 * mm, y, 'Approval Details')
    y -= 3 * mm

    # Approval card
    app_h = 20 * mm
    c.setFillColor(colors.HexColor('#f0fdf4'))
    c.roundRect(20 * mm, y - app_h, table_w, app_h, 4, fill=True, stroke=False)
    c.setStrokeColor(colors.HexColor('#bbf7d0'))
    c.setLineWidth(0.5)
    c.roundRect(20 * mm, y - app_h, table_w, app_h, 4, fill=False, stroke=True)

    ay = y - 7 * mm
    c.setFillColor(SUCCESS)
    c.setFont('Helvetica-Bold', 9)
    reviewer_name = lr.reviewed_by.get_full_name() or lr.reviewed_by.username if lr.reviewed_by else '—'
    c.drawString(26 * mm, ay, f'Approved by: {reviewer_name}')
    ay -= 5.5 * mm
    c.setFont('Helvetica', 8)
    c.setFillColor(MUTED)
    if lr.reviewed_at:
        c.drawString(26 * mm, ay, f'Date: {lr.reviewed_at.strftime("%d %B %Y at %H:%M")}')
    if lr.review_note:
        ay -= 5 * mm
        c.drawString(26 * mm, ay, f'Note: {lr.review_note[:80]}')

    y -= app_h + 20 * mm

    # ── Signature lines ──
    sig_y = y
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.8)
    # Left: Approved by
    c.line(20 * mm, sig_y, 85 * mm, sig_y)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawString(20 * mm, sig_y - 5 * mm, 'Authorized Signature')
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(20 * mm, sig_y - 10 * mm, reviewer_name)

    # Right: Employee
    c.setStrokeColor(BORDER)
    c.line(width - 85 * mm, sig_y, width - 20 * mm, sig_y)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawString(width - 85 * mm, sig_y - 5 * mm, 'Employee Signature')
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(width - 85 * mm, sig_y - 10 * mm, emp.full_name)

    # ── Footer ──
    c.setFillColor(BORDER)
    c.rect(0, 0, width, 14 * mm, fill=True, stroke=False)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 7)
    c.drawCentredString(width / 2, 6 * mm, f'{restaurant.name}  •  This document was generated electronically and is valid without a physical signature')
    c.drawCentredString(width / 2, 2.5 * mm, f'Generated on {timezone.now().strftime("%d %B %Y at %H:%M")}  •  Ref: {ref}')

    c.showPage()
    c.save()
    buf.seek(0)

    filename = f'leave_approval_{emp.employee_id}_{lr.start_date.strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Leave Types
# ---------------------------------------------------------------------------

@manager_only
def leave_type_list(request):
    types = LeaveType.objects.annotate(
        request_count=Count('requests', filter=Q(requests__status='approved')),
    )
    return render(request, 'hr/leave_type_list.html', {'leave_types': types})


@manager_only
def leave_type_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return redirect('hr-leave-type-create')
        if LeaveType.objects.filter(name__iexact=name).exists():
            messages.error(request, 'A leave type with that name already exists.')
            return redirect('hr-leave-type-create')
        days = request.POST.get('days_allowed', '0')
        try:
            days = int(days)
        except ValueError:
            days = 0
        LeaveType.objects.create(
            name=name,
            days_allowed=max(days, 0),
            is_paid=request.POST.get('is_paid') == 'on',
        )
        messages.success(request, f'Leave type "{name}" created.')
        return redirect('hr-leave-type-list')

    return render(request, 'hr/leave_type_form.html', {
        'title': 'Add Leave Type',
        'action': 'Create',
    })


@manager_only
def leave_type_edit(request, pk):
    lt = get_object_or_404(LeaveType, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return redirect('hr-leave-type-edit', pk=pk)
        dup = LeaveType.objects.filter(name__iexact=name).exclude(pk=pk)
        if dup.exists():
            messages.error(request, 'A leave type with that name already exists.')
            return redirect('hr-leave-type-edit', pk=pk)
        days = request.POST.get('days_allowed', '0')
        try:
            days = int(days)
        except ValueError:
            days = 0
        lt.name = name
        lt.days_allowed = max(days, 0)
        lt.is_paid = request.POST.get('is_paid') == 'on'
        lt.is_active = request.POST.get('is_active') == 'on'
        lt.save()
        messages.success(request, f'Leave type "{name}" updated.')
        return redirect('hr-leave-type-list')

    return render(request, 'hr/leave_type_form.html', {
        'title': f'Edit {lt.name}',
        'action': 'Save',
        'lt': lt,
    })
