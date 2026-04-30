"""
Rename 'Manager' group to 'Branch Manager' and create 'Overall Manager' group
with the same permissions.
"""

from django.db import migrations


def rename_and_create(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')

    # Rename existing Manager → Branch Manager
    try:
        manager_group = Group.objects.get(name='Manager')
        manager_group.name = 'Branch Manager'
        manager_group.save()
    except Group.DoesNotExist:
        manager_group = Group.objects.create(name='Branch Manager')

    # Create Overall Manager with same permissions as Branch Manager
    overall, created = Group.objects.get_or_create(name='Overall Manager')
    if created:
        overall.permissions.set(manager_group.permissions.all())


def reverse(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')

    # Rename back
    try:
        group = Group.objects.get(name='Branch Manager')
        group.name = 'Manager'
        group.save()
    except Group.DoesNotExist:
        pass

    # Remove Overall Manager
    Group.objects.filter(name='Overall Manager').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('branches', '0002_seed_default_branch'),
    ]

    operations = [
        migrations.RunPython(rename_and_create, reverse),
    ]
