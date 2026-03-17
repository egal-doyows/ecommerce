from django.db import migrations


def seed_defaults(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    Department = apps.get_model('hr', 'Department')

    leave_types = [
        ('Annual Leave', 21, True),
        ('Sick Leave', 14, True),
        ('Maternity Leave', 90, True),
        ('Paternity Leave', 14, True),
        ('Compassionate Leave', 5, True),
        ('Study Leave', 10, False),
        ('Unpaid Leave', 0, False),
    ]
    for name, days, is_paid in leave_types:
        LeaveType.objects.get_or_create(
            name=name,
            defaults={'days_allowed': days, 'is_paid': is_paid},
        )

    departments = [
        ('Kitchen', 'Food preparation and cooking'),
        ('Service', 'Front-of-house and customer service'),
        ('Bar', 'Beverage preparation and bar service'),
        ('Management', 'Restaurant management and administration'),
        ('Housekeeping', 'Cleaning and maintenance'),
    ]
    for name, desc in departments:
        Department.objects.get_or_create(
            name=name,
            defaults={'description': desc},
        )


def reverse_seed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_defaults, reverse_seed),
    ]
