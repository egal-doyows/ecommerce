"""
Create staff groups with standard permissions.

Owner         — Top of the chain. Same perms as Manager (Django doesn't have
                native group inheritance); reserve for the actual restaurant
                owner so the manager can be hired/fired without losing access.
Manager       — Full CRUD on everything. Auto-shift (no starting cash).
Supervisor    — Day-to-day ops, orders, shifts, stock. Auto-shift (no starting cash).
Server        — Wait staff: take orders, manage own shifts, view menu/tables.
                (Industry term — was "Front Service" pre-2026.)
Cashier       — Handle payments, manage cash drawer (starting cash required).
Kitchen       — Read incoming orders + mark items prepared/ready. No payment,
                no cash, no menu edits. Future-proofs for a KDS.
Attendant     — Cannot login. Earns commission when assigned to orders by
                Supervisors. (NOTE: this is a misuse of the Group system —
                attendants are non-login staff records, not auth principals.
                Slated to move to a model field. See backlog.)
Promoter      — Creates orders on behalf of attendants and earns commission
                on those orders. (Was "Marketing" pre-2026 — renamed because
                "Marketing" usually means social/promo, not order-taking.)

Run:  python manage.py setup_groups

Re-running is safe: legacy group names are auto-renamed in place, so user
memberships are preserved. No need to re-add staff after a rename.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


# ── Legacy → current rename map ─────────────────────────────────────
# Existing Group rows are renamed in place at the top of handle() so
# the people already in those groups don't lose access on deploy.

RENAMES = [
    ('Front Service', 'Server'),
    ('Marketing',     'Promoter'),
]


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

# Owner = same perms as Manager. Distinction is contextual ("don't fire the
# Owner") and gives you somewhere to add Owner-only views later (e.g.
# subscription, financial reports) without touching every Manager perm.
OWNER_PERMS = MANAGER_PERMS

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

SERVER_PERMS = [
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

# Kitchen / expediter — read incoming orders, mark items prepared.
# Deliberately NO cash, NO payment, NO menu edits, NO staff visibility.
# Same perms work whether the kitchen uses paper tickets today or a KDS later.
KITCHEN_PERMS = [
    ('menu', 'order',           ['view', 'change']),   # see queue + mark status
    ('menu', 'orderitem',       ['view', 'change']),   # mark per-item prepared
    ('menu', 'menuitem',        ['view']),
    ('menu', 'category',        ['view']),
    ('menu', 'recipe',          ['view']),             # ingredient lists for prep
    ('menu', 'inventoryitem',   ['view']),             # check stock when prepping
]

# Attendants cannot login. They earn commission when a Supervisor
# assigns them to an order. No Django permissions needed.
# (TODO: move out of Groups into an Employee model field — see docstring.)
ATTENDANT_PERMS = []

PROMOTER_PERMS = [
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
    ('Owner',      OWNER_PERMS),
    ('Manager',    MANAGER_PERMS),
    ('Supervisor', SUPERVISOR_PERMS),
    ('Server',     SERVER_PERMS),
    ('Cashier',    CASHIER_PERMS),
    ('Kitchen',    KITCHEN_PERMS),
    ('Attendant',  ATTENDANT_PERMS),
    ('Promoter',   PROMOTER_PERMS),
]


class Command(BaseCommand):
    help = 'Create Owner, Manager, Supervisor, Server, Cashier, Kitchen, Attendant, and Promoter groups'

    def _rename_legacy_groups(self):
        """Rename legacy Group rows in place so user memberships are preserved."""
        for old, new in RENAMES:
            try:
                old_g = Group.objects.get(name=old)
            except Group.DoesNotExist:
                continue

            new_g = Group.objects.filter(name=new).first()
            if new_g and new_g.pk != old_g.pk:
                # Both exist (someone created the new one too) — merge then drop old.
                for user in old_g.user_set.all():
                    user.groups.add(new_g)
                old_g.delete()
                self.stdout.write(self.style.WARNING(
                    f'Merged "{old}" into existing "{new}" and removed the duplicate.'
                ))
            else:
                old_g.name = new
                old_g.save()
                self.stdout.write(self.style.WARNING(
                    f'Renamed group: "{old}" → "{new}" (memberships preserved).'
                ))

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
        self._rename_legacy_groups()

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
