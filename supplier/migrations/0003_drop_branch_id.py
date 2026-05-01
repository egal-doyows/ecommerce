from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "supplier_suppliertransaction_branch_id_d5acb92c"')
    cur.execute("SELECT 1 FROM pragma_table_info('supplier_suppliertransaction') WHERE name='branch_id'")
    if cur.fetchone():
        cur.execute('ALTER TABLE "supplier_suppliertransaction" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('supplier', '0002_add_payment_allocation'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
