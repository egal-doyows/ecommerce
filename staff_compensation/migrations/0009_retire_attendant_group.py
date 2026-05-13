"""
Retire the 'Attendant' Django Group.

Historically, "Attendant" was a Group used only as a flag to mark
non-login commission-attribution staff. That misused the auth system
(see setup_groups.py docstring). This migration:

  1. Ensures every user currently in the Attendant group has a
     StaffCompensation row with is_commission_only=True.
  2. Removes those users from the Attendant group (and ensures their
     password is unusable so they can never authenticate).
  3. Deletes the Attendant Group row.

The reverse migration recreates an Attendant group and re-adds the
flagged users to it, so the change is fully invertible.
"""

from django.contrib.auth.hashers import make_password
from django.db import migrations


def retire_attendant(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    StaffCompensation = apps.get_model('staff_compensation', 'StaffCompensation')

    try:
        attendant_group = Group.objects.get(name='Attendant')
    except Group.DoesNotExist:
        return

    # Historical models from apps.get_model don't expose AbstractUser
    # helpers, so we set an unusable password directly. An unusable hash
    # starts with '!' — matches what set_unusable_password() produces.
    members = list(User.objects.filter(groups=attendant_group))
    for user in members:
        comp, _ = StaffCompensation.objects.get_or_create(
            user=user,
            defaults={
                'compensation_type': 'commission',
                'commission_scope': 'both',
                'commission_rate_regular': 0,
                'commission_rate_premium': 0,
            },
        )
        comp.is_commission_only = True
        comp.save(update_fields=['is_commission_only'])

        if not (user.password or '').startswith('!'):
            user.password = make_password(None)
            user.save(update_fields=['password'])

        user.groups.remove(attendant_group)

    attendant_group.delete()


def restore_attendant(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    StaffCompensation = apps.get_model('staff_compensation', 'StaffCompensation')

    attendant_group, _ = Group.objects.get_or_create(name='Attendant')
    flagged_users = User.objects.filter(compensation__is_commission_only=True)
    for user in flagged_users:
        user.groups.add(attendant_group)


class Migration(migrations.Migration):

    dependencies = [
        ('staff_compensation', '0008_staffcompensation_is_commission_only'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(retire_attendant, restore_attendant),
    ]
