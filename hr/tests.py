from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Department, Employee, LeaveRequest, LeaveType


class EmployeeModelTests(TestCase):
    def test_employee_id_and_active_department_count(self):
        department = Department.objects.create(name='Test Kitchen')
        active = Employee.objects.create(
            user=User.objects.create_user('chef'), department=department,
        )
        Employee.objects.create(
            user=User.objects.create_user('former-chef'), department=department,
            status='resigned',
        )

        self.assertEqual(active.employee_id, f'EMP-{active.pk:04d}')
        self.assertEqual(department.employee_count, 1)

    def test_leave_days_are_inclusive(self):
        employee = Employee.objects.create(user=User.objects.create_user('server'))
        leave_type = LeaveType.objects.create(name='Test Annual', days_allowed=21)
        request = LeaveRequest.objects.create(
            employee=employee, leave_type=leave_type,
            start_date=date(2026, 8, 3), end_date=date(2026, 8, 7),
        )

        self.assertEqual(request.days, 5)
