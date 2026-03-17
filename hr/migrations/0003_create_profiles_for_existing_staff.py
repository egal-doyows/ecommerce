from django.db import migrations


def create_profiles(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Employee = apps.get_model('hr', 'Employee')

    existing_user_ids = set(Employee.objects.values_list('user_id', flat=True))
    staff_users = User.objects.filter(is_superuser=False).exclude(pk__in=existing_user_ids)

    for i, user in enumerate(staff_users, start=1):
        # Find next available employee_id number
        last = Employee.objects.order_by('-pk').first()
        next_num = (last.pk + 1) if last else i
        Employee.objects.create(
            user=user,
            employee_id=f'EMP-{next_num:04d}',
            date_joined=user.date_joined.date() if user.date_joined else None,
            status='active' if user.is_active else 'terminated',
        )


def reverse_profiles(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0002_seed_defaults'),
    ]

    operations = [
        migrations.RunPython(create_profiles, reverse_profiles),
    ]
