from django.db import migrations, models
import django.db.models.deletion


def link_order_invoices(apps, schema_editor):
    Transaction = apps.get_model('debtor', 'DebtorTransaction')
    Order = apps.get_model('menu', 'Order')
    for invoice in Transaction.objects.filter(transaction_type='debit', order__isnull=True):
        if not invoice.reference or not invoice.reference.isdigit():
            continue
        order = Order.objects.filter(pk=int(invoice.reference), debtor_id=invoice.debtor_id).first()
        if order and not Transaction.objects.filter(order_id=order.id).exists():
            invoice.order_id = order.id
            invoice.save(update_fields=['order'])


class Migration(migrations.Migration):
    dependencies = [
        ('debtor', '0004_debtor_created_by'),
        ('menu', '0045_shift_one_active_shift_per_waiter'),
    ]

    operations = [
        migrations.AddField(
            model_name='debtortransaction', name='order',
            field=models.OneToOneField(blank=True, help_text='Order that created this invoice, when applicable.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='credit_invoice', to='menu.order'),
        ),
        migrations.AddField(
            model_name='debtortransaction', name='payment_method',
            field=models.CharField(blank=True, choices=[('cash', 'Cash'), ('mpesa', 'M-Pesa'), ('card', 'Card')], help_text='Tender used for payment transactions.', max_length=10),
        ),
        migrations.AddField(
            model_name='debtortransaction', name='payment_reference',
            field=models.CharField(blank=True, help_text='M-Pesa code, card authorization, or other tender reference.', max_length=50),
        ),
        migrations.RunPython(link_order_invoices, migrations.RunPython.noop),
    ]
