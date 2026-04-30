from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='headed_departments',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def employee_count(self):
        return self.employees.filter(status='active').count()


# ---------------------------------------------------------------------------
# Position / Designation
# ---------------------------------------------------------------------------

class Position(models.Model):
    title = models.CharField(max_length=100, unique=True)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='positions',
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Employee Profile
# ---------------------------------------------------------------------------

class Employee(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]
    MARITAL_CHOICES = [
        ('single', 'Single'),
        ('married', 'Married'),
        ('divorced', 'Divorced'),
        ('widowed', 'Widowed'),
    ]
    EMPLOYMENT_TYPE_CHOICES = [
        ('full_time', 'Full Time'),
        ('part_time', 'Part Time'),
        ('contract', 'Contract'),
        ('casual', 'Casual'),
        ('intern', 'Intern'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('on_leave', 'On Leave'),
        ('suspended', 'Suspended'),
        ('terminated', 'Terminated'),
        ('resigned', 'Resigned'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='hr_profile',
    )
    employee_id = models.CharField(max_length=20, unique=True, blank=True)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='employees',
    )
    position = models.ForeignKey(
        Position, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='employees',
    )

    # Personal info
    phone = models.CharField(max_length=20, blank=True)
    alt_phone = models.CharField(max_length=20, blank=True, verbose_name='Alternative phone')
    personal_email = models.EmailField(blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True)
    marital_status = models.CharField(max_length=10, choices=MARITAL_CHOICES, blank=True)
    national_id = models.CharField(max_length=30, blank=True, verbose_name='National ID / Passport')
    address = models.TextField(blank=True)

    # Employment info
    employment_type = models.CharField(max_length=15, choices=EMPLOYMENT_TYPE_CHOICES, default='full_time')
    date_joined = models.DateField(default=timezone.now)
    date_left = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active')
    notes = models.TextField(blank=True, verbose_name='HR Notes')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__first_name', 'user__last_name']

    def __str__(self):
        return self.full_name or self.user.username

    @property
    def full_name(self):
        fn = self.user.get_full_name()
        return fn if fn.strip() else self.user.username

    @property
    def years_of_service(self):
        end = self.date_left or timezone.now().date()
        delta = end - self.date_joined
        return round(delta.days / 365.25, 1)

    def save(self, *args, **kwargs):
        if not self.employee_id:
            last = Employee.objects.order_by('-pk').first()
            next_num = (last.pk + 1) if last else 1
            self.employee_id = f'EMP-{next_num:04d}'
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Emergency Contact
# ---------------------------------------------------------------------------

class EmergencyContact(models.Model):
    RELATIONSHIP_CHOICES = [
        ('spouse', 'Spouse'),
        ('parent', 'Parent'),
        ('sibling', 'Sibling'),
        ('child', 'Child'),
        ('friend', 'Friend'),
        ('other', 'Other'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='emergency_contacts',
    )
    name = models.CharField(max_length=100)
    relationship = models.CharField(max_length=15, choices=RELATIONSHIP_CHOICES, default='other')
    phone = models.CharField(max_length=20)
    alt_phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.get_relationship_display()})'


# ---------------------------------------------------------------------------
# Leave Management
# ---------------------------------------------------------------------------

class LeaveType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    days_allowed = models.PositiveIntegerField(
        default=0, help_text='Annual allocation (0 = unlimited)',
    )
    is_paid = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='leave_requests',
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.CASCADE, related_name='requests',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reviewed_leaves',
    )
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.employee.full_name} — {self.leave_type.name} ({self.start_date} to {self.end_date})'

    @property
    def days(self):
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 0


# ---------------------------------------------------------------------------
# Document Management
# ---------------------------------------------------------------------------

class Document(models.Model):
    CATEGORY_CHOICES = [
        ('contract', 'Employment Contract'),
        ('id', 'ID / Passport Copy'),
        ('certificate', 'Certificate'),
        ('letter', 'Letter'),
        ('medical', 'Medical Record'),
        ('other', 'Other'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='documents',
    )
    title = models.CharField(max_length=150)
    category = models.CharField(max_length=15, choices=CATEGORY_CHOICES, default='other')
    file = models.FileField(upload_to='hr/documents/%Y/%m/')
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.title
