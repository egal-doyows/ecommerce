from decimal import Decimal

from django.db import migrations, models


def backfill_unit_cost(apps, schema_editor):
    """
    Best-effort backfill of historical OrderItems with today's COGS.
    Approximate (buying_price drifts), but better than zero for legacy rows.
    """
    OrderItem = apps.get_model('menu', 'OrderItem')
    Recipe = apps.get_model('menu', 'Recipe')

    for oi in OrderItem.objects.select_related('menu_item__inventory_item').iterator():
        mi = oi.menu_item
        if mi.inventory_item_id is not None:
            cost = mi.inventory_item.buying_price
        else:
            cost = Decimal('0')
            for r in Recipe.objects.filter(menu_item=mi).select_related('inventory_item'):
                cost += r.quantity_required * r.inventory_item.buying_price
        if cost:
            oi.unit_cost = cost
            oi.save(update_fields=['unit_cost'])


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0016_drop_orphaned_branch_and_tax_columns'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='unit_cost',
            field=models.DecimalField(decimal_places=4, default=0, help_text='COGS per unit, snapshotted at order time', max_digits=10),
        ),
        migrations.RunPython(backfill_unit_cost, reverse_code=migrations.RunPython.noop),
    ]
