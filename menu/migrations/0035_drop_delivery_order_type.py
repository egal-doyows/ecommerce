from django.db import migrations, models


def delivery_to_takeaway(apps, schema_editor):
    Order = apps.get_model('menu', 'Order')
    Order.objects.filter(order_type='delivery').update(order_type='takeaway')


def takeaway_to_delivery(apps, schema_editor):
    # Reverse is lossy — leave rows as takeaway on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0034_restaurantsettings_tax_number'),
    ]

    operations = [
        migrations.RunPython(delivery_to_takeaway, takeaway_to_delivery),
        migrations.AlterField(
            model_name='order',
            name='order_type',
            field=models.CharField(
                choices=[('dine_in', 'Dine-in'), ('takeaway', 'Takeaway')],
                default='dine_in',
                max_length=10,
            ),
        ),
    ]
