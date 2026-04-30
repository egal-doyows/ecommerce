from django.db import migrations


def backfill(apps, schema_editor):
    Order = apps.get_model('menu', 'Order')
    orders = Order.objects.filter(order_number='').order_by('created_at')
    daily_counts = {}
    for order in orders:
        day = order.created_at.date()
        daily_counts[day] = daily_counts.get(day, 0) + 1
        date_str = day.strftime('%Y%m%d')
        order.order_number = f'ORD-{date_str}-{daily_counts[day]:03d}'
        order.save(update_fields=['order_number'])


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0020_add_order_number'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
