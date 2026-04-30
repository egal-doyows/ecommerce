"""
Create staff groups with standard permissions.

Manager       — Full CRUD on everything. Auto-shift (no starting cash).
Supervisor    — Day-to-day ops, orders, shifts, stock. Auto-shift (no starting cash).
Front Service — Waiters: take orders, manage own shifts, view menu/tables.
Cashier       — Handle payments, manage cash drawer (starting cash required).
Attendant     — Cannot login. Earns commission when assigned to orders by Supervisors.
Marketing     — Creates orders on behalf of attendants. Earns commission on orders they create.

Run:  python manage.py setup_groups
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


# ── Permission mapping ──────────────────────────────────────────────
# Format: (app_label, model_name, [codename, ...])

MANAGER_PERMS = [
    ('menu', 'category',        ['add', 'change', 'delete', 'view']),
    ('menu', 'menuitem',        ['add', 'change', 'delete', 'view']),
    ('menu', 'recipe',          ['add', 'change', 'delete', 'view']),
    ('menu', 'inventoryitem',   ['add', 'change', 'delete', 'view']),
    ('menu', 'table',           ['add', 'change', 'delete', 'view']),
    ('menu', 'order',           ['add', 'change', 'delete', 'view']),
    ('menu', 'orderitem',       ['add', 'change', 'delete', 'view']),
    ('menu', 'shift',           ['add', 'change', 'delete', 'view']),
    ('menu', 'restaurantsettings', ['add', 'change', 'delete', 'view']),
    ('auth', 'user',            ['add', 'change', 'delete', 'view']),
    ('account', 'waitercode',   ['add', 'change', 'delete', 'view']),
    ('staff_compensation', 'staffcompensation', ['add', 'change', 'delete', 'view']),
    ('staff_compensation', 'paymentrecord',     ['add', 'change', 'delete', 'view']),
]

SUPERVISOR_PERMS = [
    # Orders — create, view, manage status
    ('menu', 'order',           ['add', 'change', 'view']),
    ('menu', 'orderitem',       ['add', 'change', 'delete', 'view']),
    # Shifts — view and manage
    ('menu', 'shift',           ['add', 'change', 'view']),
    # Tables — manage availability
    ('menu', 'table',           ['change', 'view']),
    # Inventory — update stock levels, view
    ('menu', 'inventoryitem',   ['change', 'view']),
    # Menu/categories — read only
    ('menu', 'menuitem',        ['view']),
    ('menu', 'category',        ['view']),
    ('menu', 'recipe',          ['view']),
    # Staff — view only
    ('auth', 'user',            ['view']),
    ('account', 'waitercode',   ['view']),
    # Compensation — view + mark paid
    ('staff_compensation', 'staffcompensation', ['view']),
    ('staff_compensation', 'paymentrecord',     ['change', 'view']),
]

FRONT_SERVICE_PERMS = [
    # Orders — create and manage own
    ('menu', 'order',           ['add', 'change', 'view']),
    ('menu', 'orderitem',       ['add', 'change', 'delete', 'view']),
    # Shifts — own shifts
    ('menu', 'shift',           ['add', 'view']),
    # Tables — view and toggle reserve
    ('menu', 'table',           ['change', 'view']),
    # Menu & categories — read only
    ('menu', 'menuitem',        ['view']),
    ('menu', 'category',        ['view']),
    ('menu', 'recipe',          ['view']),
    # Inventory — view stock levels
    ('menu', 'inventoryitem',   ['view']),
]

CASHIER_PERMS = [
    # Orders — process payments, view all
    ('menu', 'order',           ['change', 'view']),
    ('menu', 'orderitem',       ['view']),
    # Shifts — own shifts (with starting cash)
    ('menu', 'shift',           ['add', 'view']),
    # Tables — view
    ('menu', 'table',           ['view']),
    # Menu — view
    ('menu', 'menuitem',        ['view']),
    ('menu', 'category',        ['view']),
]

# Attendants cannot login. They earn commission when a Supervisor
# assigns them to an order.  No Django permissions needed.
ATTENDANT_PERMS = []

MARKETING_PERMS = [
    # Orders — create and manage own
    ('menu', 'order',           ['add', 'change', 'view']),
    ('menu', 'orderitem',       ['add', 'change', 'delete', 'view']),
    # Shifts — own shifts
    ('menu', 'shift',           ['add', 'view']),
    # Tables — view and toggle reserve
    ('menu', 'table',           ['change', 'view']),
    # Menu & categories — read only
    ('menu', 'menuitem',        ['view']),
    ('menu', 'category',        ['view']),
    ('menu', 'recipe',          ['view']),
    # Inventory — view stock levels
    ('menu', 'inventoryitem',   ['view']),
]

GROUPS = [
    ('Manager',       MANAGER_PERMS),
    ('Supervisor',    SUPERVISOR_PERMS),
    ('Front Service', FRONT_SERVICE_PERMS),
    ('Cashier',       CASHIER_PERMS),
    ('Attendant',     ATTENDANT_PERMS),
    ('Marketing',     MARKETING_PERMS),
]


class Command(BaseCommand):
    help = 'Create Manager, Supervisor, Front Service, Cashier, Attendant, and Marketing groups'

    def _resolve_perms(self, perm_map):
        perm_objects = []
        for app, model, actions in perm_map:
            ct = ContentType.objects.get(app_label=app, model=model)
            for action in actions:
                codename = f'{action}_{model}'
                try:
                    perm = Permission.objects.get(content_type=ct, codename=codename)
                    perm_objects.append(perm)
                except Permission.DoesNotExist:
                    self.stderr.write(
                        self.style.WARNING(f'  Permission not found: {codename} ({app}.{model})')
                    )
        return perm_objects

    def handle(self, *args, **options):
        for group_name, perm_map in GROUPS:
            group, created = Group.objects.get_or_create(name=group_name)
            perms = self._resolve_perms(perm_map)
            group.permissions.set(perms)
            status = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(
                f'{status} "{group_name}" — {len(perms)} permissions'
            ))

        self.stdout.write('')
        for group_name, _ in GROUPS:
            group = Group.objects.get(name=group_name)
            self.stdout.write(self.style.MIGRATE_HEADING(f'{group_name} permissions:'))
            for p in group.permissions.order_by('codename'):
                self.stdout.write(f'  {p.content_type.app_label}.{p.codename}')
            self.stdout.write('')
