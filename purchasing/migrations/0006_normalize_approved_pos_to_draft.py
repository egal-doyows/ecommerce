from django.db import migrations


def normalize_to_draft(apps, schema_editor):
    """The approval workflow was removed — POs now flow draft → received.
    Collapse any legacy 'pending'/'approved' orders into 'draft' so they are
    receivable and editable under the new flow."""
    PurchaseOrder = apps.get_model('purchasing', 'PurchaseOrder')
    PurchaseOrder.objects.filter(status__in=['pending', 'approved']).update(status='draft')


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0005_remove_purchaseorderitem_received_quantity'),
    ]

    operations = [
        # Reverse is a no-op: the original draft/pending/approved distinction
        # can't be reconstructed, and 'draft' is valid in both directions.
        migrations.RunPython(normalize_to_draft, migrations.RunPython.noop),
    ]
