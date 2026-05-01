from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    cur.execute('DROP INDEX IF EXISTS "supplier_suppliertransaction_branch_id_d5acb92c"')
    if has_col('supplier_suppliertransaction', 'branch_id'):
        cur.execute('ALTER TABLE "supplier_suppliertransaction" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('supplier', '0002_add_payment_allocation'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
