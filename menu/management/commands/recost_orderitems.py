"""
Re-stamp the frozen COGS (OrderItem.unit_cost) for a single menu item.

OrderItem.unit_cost is snapshotted at order time and never changes again,
so correcting a menu item's cost (its inventory_item.buying_price or recipe)
only affects FUTURE orders — historical COGS stays at the old, possibly
wrong, value. This command fixes that for one item: it recomputes the BASE
cost from today's corrected master data and re-stamps it onto every existing
order line for that item, while preserving each line's frozen accompaniment
(option) costs exactly as they were captured.

    new unit_cost = menu_item.current_unit_cost()        # corrected base
                    + Σ(OrderItemOption.unit_cost)        # options, untouched

This mirrors exactly how the freeze is built at order time
(menu/views.py / menu/api.py), so only the part that was wrong — the base —
is refreshed.

DRY RUN BY DEFAULT. Nothing is written unless you pass --commit. The dry run
prints the line count and the COGS impact (on paid, non-comped orders — the
figure the P&L / COGS-detail reports show) so you can preview before applying.

Examples:
    python manage.py recost_orderitems 42            # by MenuItem id, dry run
    python manage.py recost_orderitems "Cheeseburger" # by title (icontains)
    python manage.py recost_orderitems 42 --commit    # apply the change
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Sum, DecimalField
from django.db.models.functions import Coalesce

from menu.models import MenuItem, OrderItem


def _money(value):
    return f'{value:,.2f}'


class Command(BaseCommand):
    help = "Recompute frozen OrderItem.unit_cost for one menu item from corrected master cost."

    def add_arguments(self, parser):
        parser.add_argument(
            'item',
            help="MenuItem id, or a title fragment (case-insensitive) that matches exactly one item.",
        )
        parser.add_argument(
            '--commit', action='store_true',
            help="Write the changes. Without this flag the command only previews (dry run).",
        )

    def _resolve_item(self, raw):
        # Numeric → treat as primary key first.
        if raw.isdigit():
            try:
                return MenuItem.objects.get(pk=int(raw))
            except MenuItem.DoesNotExist:
                pass  # fall through to title match (an item literally named "42")

        matches = list(MenuItem.objects.filter(title__icontains=raw))
        if not matches:
            raise CommandError(f'No menu item matches id/title {raw!r}.')
        if len(matches) > 1:
            listing = '\n'.join(f'  {m.id}  {m.title}' for m in matches)
            raise CommandError(
                f'{raw!r} matches {len(matches)} items — re-run with the exact id:\n{listing}'
            )
        return matches[0]

    @transaction.atomic
    def handle(self, *args, **opts):
        item = self._resolve_item(opts['item'])
        new_base = item.current_unit_cost()

        self.stdout.write(f'Menu item:      [{item.id}] {item.title}')
        self.stdout.write(f'Corrected base unit cost (from master): {_money(new_base)}')

        # All order lines for this item, across all history.
        lines = list(
            OrderItem.objects.filter(menu_item=item)
            .prefetch_related('options')
        )
        if not lines:
            self.stdout.write(self.style.WARNING('No order lines for this item — nothing to do.'))
            return

        # Per-line new cost = corrected base + the line's already-frozen option costs.
        # Σ option costs is read straight off OrderItemOption.unit_cost so the
        # accompaniment portion is preserved exactly as captured at order time.
        changed = []
        for oi in lines:
            option_cost = sum((o.unit_cost for o in oi.options.all()), Decimal('0'))
            new_unit_cost = (new_base + option_cost).quantize(Decimal('0.0001'))
            if new_unit_cost != oi.unit_cost:
                changed.append((oi, oi.unit_cost, new_unit_cost))

        # COGS impact on the report scope: paid, non-comped orders.
        report_ids = set(
            OrderItem.objects.filter(
                menu_item=item, order__status='paid', order__is_comp=False,
            ).values_list('id', flat=True)
        )
        old_report_cogs = Decimal('0')
        new_report_cogs = Decimal('0')
        for oi, old, new in changed:
            if oi.id in report_ids:
                old_report_cogs += old * oi.quantity
                new_report_cogs += new * oi.quantity

        self.stdout.write('')
        self.stdout.write(f'Order lines for this item:          {len(lines)}')
        self.stdout.write(f'Lines whose unit_cost will change:  {len(changed)}')
        self.stdout.write('Reported COGS for this item (paid, non-comped orders):')
        self.stdout.write(f'    before: {_money(old_report_cogs)}')
        self.stdout.write(f'    after:  {_money(new_report_cogs)}')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'    change: {_money(new_report_cogs - old_report_cogs)}'
        ))

        # Show a few example lines so the operator can sanity-check.
        for oi, old, new in changed[:5]:
            self.stdout.write(f'      line #{oi.id} (order {oi.order_id}, qty {oi.quantity}): '
                              f'{_money(old)} → {_money(new)}')
        if len(changed) > 5:
            self.stdout.write(f'      … and {len(changed) - 5} more')

        if not changed:
            self.stdout.write(self.style.SUCCESS('Already up to date — nothing to write.'))
            return

        if not opts['commit']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'DRY RUN — no changes written. Re-run with --commit to apply.'
            ))
            return

        for oi, _old, new in changed:
            oi.unit_cost = new
            oi.save(update_fields=['unit_cost'])

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Committed: re-stamped unit_cost on {len(changed)} order line(s).'
        ))
