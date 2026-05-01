from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "purchasing_purchaseorder_branch_id_5ad6e832"')
    cur.execute("SELECT 1 FROM pragma_table_info('purchasing_purchaseorder') WHERE name='branch_id'")
    if cur.fetchone():
        cur.execute('ALTER TABLE "purchasing_purchaseorder" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0003_change_received_date_to_datetime'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
