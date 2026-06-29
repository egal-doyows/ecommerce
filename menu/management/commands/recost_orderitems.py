"""
Re-stamp the frozen COGS (OrderItem.unit_cost) for orders in a date window.

OrderItem.unit_cost is snapshotted at order time and never changes again, so
correcting a menu item's cost (its inventory_item.buying_price or recipe) only
affects FUTURE orders — historical COGS stays at the old, possibly wrong,
value. This command fixes that for every order line in a window: it recomputes
each line's BASE cost from today's corrected master data and re-stamps it,
while preserving each line's frozen accompaniment (option) costs exactly as
they were captured.

    new unit_cost = menu_item.current_unit_cost()        # corrected base
                    + Σ(OrderItemOption.unit_cost)        # options, untouched

This mirrors exactly how the freeze is built at order time
(menu/views.py / menu/api.py), so only the part that was wrong — the base —
is refreshed.

NOTE: recompute uses TODAY's master cost for every item. Within a short window
(the default is the last 30 days) that's the intended correction; over a long
window it would flatten any genuine cost changes to today's value, so keep the
window tight — just wide enough to cover the bad data.

DRY RUN BY DEFAULT. Nothing is written unless you pass --commit. The dry run
prints a per-item breakdown of the COGS impact (on paid, non-comped orders —
the figure the P&L / COGS-detail reports show) so you can preview first.

Examples:
    python manage.py recost_orderitems                       # last 30 days, dry run
    python manage.py recost_orderitems --days 45             # last 45 days
    python manage.py recost_orderitems --start 2026-05-01 --end 2026-05-31
    python manage.py recost_orderitems --item "Plate of plantain"  # one item only
    python manage.py recost_orderitems --commit              # apply (last 30 days)
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from menu.models import MenuItem, OrderItem


def _money(value):
    return f'{value:,.2f}'


def _parse_date(raw):
    try:
        return timezone.datetime.strptime(raw, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        raise CommandError(f'Invalid date {raw!r} — use YYYY-MM-DD.')


class Command(BaseCommand):
    help = "Recompute frozen OrderItem.unit_cost from corrected master cost for a date window."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=30,
            help="Window = the last N days (default 30). Ignored if --start/--end are given.",
        )
        parser.add_argument('--start', help="Window start date YYYY-MM-DD (inclusive).")
        parser.add_argument('--end', help="Window end date YYYY-MM-DD (inclusive).")
        parser.add_argument(
            '--item',
            help="Optional: restrict to one MenuItem (id, or a title fragment matching exactly one).",
        )
        parser.add_argument(
            '--commit', action='store_true',
            help="Write the changes. Without this flag the command only previews (dry run).",
        )

    def _resolve_item(self, raw):
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
        # Resolve the date window.
        if opts['start'] or opts['end']:
            if not (opts['start'] and opts['end']):
                raise CommandError('Pass both --start and --end, or neither.')
            start, end = _parse_date(opts['start']), _parse_date(opts['end'])
            if start > end:
                raise CommandError('--start must be on or before --end.')
        else:
            end = timezone.localdate()
            start = end - timedelta(days=opts['days'])

        self.stdout.write(f'Window: {start} → {end} (inclusive)')

        lines_qs = (
            OrderItem.objects
            .filter(order__created_at__date__gte=start, order__created_at__date__lte=end)
            .select_related('menu_item', 'menu_item__inventory_item')
            .prefetch_related('menu_item__recipe_items__inventory_item', 'options')
        )

        if opts['item']:
            item = self._resolve_item(opts['item'])
            lines_qs = lines_qs.filter(menu_item=item)
            self.stdout.write(f'Restricted to item: [{item.id}] {item.title}')

        lines = list(lines_qs)
        if not lines:
            self.stdout.write(self.style.WARNING('No order lines in this window — nothing to do.'))
            return

        # Cache the corrected base cost per menu item (current_unit_cost is the
        # same for every line of a given item, so compute it once).
        base_cache = {}

        def base_cost(mi):
            if mi.id not in base_cache:
                base_cache[mi.id] = mi.current_unit_cost()
            return base_cache[mi.id]

        # Per-line new cost = corrected base + the line's already-frozen option
        # costs (read straight off OrderItemOption.unit_cost, so the
        # accompaniment portion is preserved exactly as captured at order time).
        changed = []
        for oi in lines:
            if oi.menu_item_id is None:
                continue
            option_cost = sum((o.unit_cost for o in oi.options.all()), Decimal('0'))
            new_unit_cost = (base_cost(oi.menu_item) + option_cost).quantize(Decimal('0.0001'))
            if new_unit_cost != oi.unit_cost:
                changed.append((oi, oi.unit_cost, new_unit_cost))

        # COGS impact, aggregated per item, on the report scope: paid, non-comped.
        report_ids = set(
            OrderItem.objects.filter(
                id__in=[oi.id for oi, _o, _n in changed],
                order__status='paid', order__is_comp=False,
            ).values_list('id', flat=True)
        )
        per_item = {}  # menu_item_id -> {title, lines, before, after}
        for oi, old, new in changed:
            if oi.id not in report_ids:
                continue
            d = per_item.setdefault(oi.menu_item_id, {
                'title': oi.menu_item.title, 'lines': 0,
                'before': Decimal('0'), 'after': Decimal('0'),
            })
            d['lines'] += 1
            d['before'] += old * oi.quantity
            d['after'] += new * oi.quantity

        self.stdout.write('')
        self.stdout.write(f'Order lines scanned:                {len(lines)}')
        self.stdout.write(f'Lines whose unit_cost will change:  {len(changed)}')

        if not changed:
            self.stdout.write(self.style.SUCCESS('Already up to date — nothing to write.'))
            return

        self.stdout.write('')
        self.stdout.write('COGS impact per item (paid, non-comped orders):')
        total_before = total_after = Decimal('0')
        for d in sorted(per_item.values(), key=lambda x: x['after'] - x['before']):
            delta = d['after'] - d['before']
            total_before += d['before']
            total_after += d['after']
            self.stdout.write(
                f"    {d['title'][:34]:<34}  {d['lines']:>4} lines   "
                f"{_money(d['before']):>14} → {_money(d['after']):>14}   "
                f"Δ {_money(delta):>14}"
            )
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"    {'TOTAL':<34}  {'':>4}        "
            f"{_money(total_before):>14} → {_money(total_after):>14}   "
            f"Δ {_money(total_after - total_before):>14}"
        ))

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
