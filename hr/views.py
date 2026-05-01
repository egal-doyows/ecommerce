import os
from datetime import datetime
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import get_valid_filename

from menu.models import RestaurantSettings

from .models import (
    Department, Document, EmergencyContact, Employee,
    LeaveRequest, LeaveType, Position,
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _is_manager(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name='Manager').exists()
    )


def _is_admin_user(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )


def manager_only(view_func):
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager(request.user):
            messages.error(request, 'Only managers can access this page.')
            return redirect('hr-dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def hr_staff_required(view_func):
    """Managers and Supervisors can access HR views."""
    @wraps(view_func)
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_admin_user(request.user):
            messages.error(request, 'You do not have permission to access HR.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@hr_staff_required
def hr_dashboard(request):
    today = timezone.now().date()
    total_employees = Employee.objects.filter(status='active').count()
    on_leave = Employee.objects.filter(status='on_leave').count()
    departments = Department.objects.filter(is_active=True).count()
    pending_leaves = LeaveRequest.objects.filter(status='pending').count()

    # Recent hires (last 30 days)
    from datetime import timedelta
    recent_hires = Employee.objects.filter(
        status='active',
        date_joined__gte=today - timedelta(days=30),
    ).select_related('user', 'department', 'position')[:5]

    # Upcoming birthdays (next 30 days)
    upcoming_birthdays = []
    for emp in Employee.objects.filter(status='active', date_of_birth__isnull=False).select_related('user'):
        bday_this_year = emp.date_of_birth.replace(year=today.year)
        if bday_this_year < today:
            bday_this_year = bday_this_year.replace(year=today.year + 1)
        diff = (bday_this_year - today).days
        if 0 <= diff <= 30:
            upcoming_birthdays.append({'employee': emp, 'date': bday_this_year, 'days': diff})
    upcoming_birthdays.sort(key=lambda x: x['days'])

    # Department breakdown
    dept_stats = Department.objects.filter(is_active=True).annotate(
        emp_count=Count('employees', filter=Q(employees__status='active')),
    ).order_by('-emp_count')

    # Employment type breakdown
    type_stats = (
        Employee.objects.filter(status='active')
        .values('employment_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # Pending leave requests
    pending_leave_list = LeaveRequest.objects.filter(
        status='pending',
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
        'is_manager': _is_manager(request.user),
    })


# ---------------------------------------------------------------------------
# Employee List
# ---------------------------------------------------------------------------

@hr_staff_required
def employee_list(request):
    qs = Employee.objects.select_related('user', 'department', 'position').prefetch_related('user__groups')

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
        'departments': Department.objects.filter(is_active=True),
        'status_choices': Employee.STATUS_CHOICES,
        'type_choices': Employee.EMPLOYMENT_TYPE_CHOICES,
        'is_manager': _is_manager(request.user),
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

        emp = Employee.objects.create(
            user=user,
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

    return render(request, 'hr/employee_form.html', {
        'title': 'Create Employee Profile',
        'available_users': available_users,
        'departments': Department.objects.filter(is_active=True),
        'positions': Position.objects.filter(is_active=True),
        'gender_choices': Employee.GENDER_CHOICES,
        'marital_choices': Employee.MARITAL_CHOICES,
        'type_choices': Employee.EMPLOYMENT_TYPE_CHOICES,
        'today': timezone.now().date().isoformat(),
        'is_create': True,
    })


# ---------------------------------------------------------------------------
# Employee Detail
# ---------------------------------------------------------------------------

@hr_staff_required
def employee_detail(request, pk):
    emp = get_object_or_404(
        Employee.objects.select_related('user', 'department', 'position'),
        pk=pk,
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

    return render(request, 'hr/employee_detail.html', {
        'emp': emp,
        'contacts': contacts,
        'documents': documents,
        'leaves': leaves,
        'leave_summary': leave_summary,
        'is_manager': _is_manager(request.user),
        'doc_categories': Document.CATEGORY_CHOICES,
        'departments': Department.objects.filter(is_active=True),
        'positions': Position.objects.filter(is_active=True).select_related('department'),
    })


# ---------------------------------------------------------------------------
# Employee Edit
# ---------------------------------------------------------------------------

@manager_only
def employee_edit(request, pk):
    emp = get_object_or_404(Employee.objects.select_related('user'), pk=pk)

    if request.method == 'POST':
        emp.user.first_name = request.POST.get('first_name', '').strip()
        emp.user.last_name = request.POST.get('last_name', '').strip()
        emp.user.email = request.POST.get('email', '').strip()
        emp.user.save()

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

    return render(request, 'hr/employee_form.html', {
        'title': f'Edit {emp.full_name}',
        'emp': emp,
        'departments': Department.objects.filter(is_active=True),
        'positions': Position.objects.filter(is_active=True),
        'gender_choices': Employee.GENDER_CHOICES,
        'marital_choices': Employee.MARITAL_CHOICES,
        'type_choices': Employee.EMPLOYMENT_TYPE_CHOICES,
        'is_create': False,
    })


# ---------------------------------------------------------------------------
# Employee Status Change
# ---------------------------------------------------------------------------

@manager_only
def employee_status(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
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
    emp = get_object_or_404(Employee.objects.select_related('department', 'position'), pk=pk)
    if request.method == 'POST':
        old_dept = emp.department.name if emp.department else 'None'
        old_pos = emp.position.title if emp.position else 'None'

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

        note = request.POST.get('transfer_note', '').strip()
        new_dept = emp.department.name if emp.department else 'None'
        new_pos = emp.position.title if emp.position else 'None'

        # Append transfer note to HR notes
        if note or old_dept != new_dept or old_pos != new_pos:
            transfer_log = f'[{timezone.now().date()}] Transferred: {old_dept}/{old_pos} → {new_dept}/{new_pos}'
            if note:
                transfer_log += f' — {note}'
            emp.notes = f'{transfer_log}\n{emp.notes}'.strip() if emp.notes else transfer_log

        emp.save()
        messages.success(request, f'{emp.full_name} transferred to {new_dept} / {new_pos}.')
    return redirect('hr-employee-detail', pk=pk)


# ---------------------------------------------------------------------------
# Emergency Contacts
# ---------------------------------------------------------------------------

@manager_only
def emergency_contact_add(request, emp_pk):
    emp = get_object_or_404(Employee, pk=emp_pk)
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
    contact = get_object_or_404(EmergencyContact, pk=pk)
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
    contact = get_object_or_404(EmergencyContact, pk=pk)
    emp_pk = contact.employee_id
    if request.method == 'POST':
        contact.delete()
        messages.success(request, 'Emergency contact removed.')
    return redirect('hr-employee-detail', pk=emp_pk)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

ALLOWED_DOCUMENT_EXTENSIONS = {
    '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp',
    '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt',
}
MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


@manager_only
def document_upload(request, emp_pk):
    emp = get_object_or_404(Employee, pk=emp_pk)
    if request.method == 'POST' and request.FILES.get('file'):
        upload = request.FILES['file']
        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            messages.error(
                request,
                f'File type "{ext or "unknown"}" not allowed. '
                f'Use PDF, image, or office documents.',
            )
            return redirect('hr-employee-detail', pk=emp_pk)
        if upload.size > MAX_DOCUMENT_SIZE_BYTES:
            messages.error(request, 'File too large (max 10 MB).')
            return redirect('hr-employee-detail', pk=emp_pk)
        upload.name = get_valid_filename(os.path.basename(upload.name))
        Document.objects.create(
            employee=emp,
            title=request.POST.get('title', '').strip() or upload.name,
            category=request.POST.get('category', 'other'),
            file=upload,
            notes=request.POST.get('notes', '').strip(),
            uploaded_by=request.user,
        )
        messages.success(request, 'Document uploaded.')
    return redirect('hr-employee-detail', pk=emp_pk)


@manager_only
def document_delete(request, pk):
    doc = get_object_or_404(Document, pk=pk)
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
        Q(is_superuser=True) | Q(groups__name='Manager'),
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
        Q(is_superuser=True) | Q(groups__name='Manager'),
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

@hr_staff_required
def leave_list(request):
    qs = LeaveRequest.objects.select_related(
        'employee__user', 'leave_type', 'reviewed_by',
    )

    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    search = request.GET.get('q', '')

    if status_filter:
        qs = qs.filter(status=status_filter)
    if type_filter:
        qs = qs.filter(leave_type_id=type_filter)
    if search:
        qs = qs.filter(
            Q(employee__user__first_name__icontains=search)
            | Q(employee__user__last_name__icontains=search)
            | Q(employee__user__username__icontains=search)
        )

    pending_count = LeaveRequest.objects.filter(status='pending').count()

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
        'is_manager': _is_manager(request.user),
    })


@hr_staff_required
def leave_request(request):
    if request.method == 'POST':
        # Determine the employee
        emp = None
        if _is_manager(request.user):
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

    employees = Employee.objects.filter(status='active').select_related('user') if _is_manager(request.user) else None

    return render(request, 'hr/leave_form.html', {
        'title': 'Request Leave',
        'leave_types': LeaveType.objects.filter(is_active=True),
        'employees': employees,
        'is_manager': _is_manager(request.user),
        'today': timezone.now().date().isoformat(),
    })


@hr_staff_required
def leave_detail(request, pk):
    lr = get_object_or_404(
        LeaveRequest.objects.select_related(
            'employee__user', 'employee__department', 'leave_type', 'reviewed_by',
        ),
        pk=pk,
    )
    return render(request, 'hr/leave_detail.html', {
        'lr': lr,
        'is_manager': _is_manager(request.user),
    })


@manager_only
def leave_approve(request, pk):
    lr = get_object_or_404(LeaveRequest, pk=pk)
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
    lr = get_object_or_404(LeaveRequest, pk=pk)
    if request.method == 'POST' and lr.status == 'pending':
        lr.status = 'rejected'
        lr.reviewed_by = request.user
        lr.review_note = request.POST.get('note', '').strip()
        lr.reviewed_at = timezone.now()
        lr.save()
        messages.success(request, f'Leave rejected for {lr.employee.full_name}.')
    return redirect('hr-leave-detail', pk=pk)


@hr_staff_required
def leave_cancel(request, pk):
    lr = get_object_or_404(LeaveRequest, pk=pk)
    if request.method == 'POST' and lr.status == 'pending':
        # Only the requester or a manager can cancel
        is_own = hasattr(request.user, 'hr_profile') and lr.employee == request.user.hr_profile
        if is_own or _is_manager(request.user):
            lr.status = 'cancelled'
            lr.save()
            messages.success(request, 'Leave request cancelled.')
    return redirect('hr-leave-detail', pk=pk)


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
