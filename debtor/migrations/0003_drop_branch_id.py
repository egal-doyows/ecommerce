from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    cur.execute('DROP INDEX IF EXISTS "debtor_debtor_branch_id_d57f7159"')
    if has_col('debtor_debtor', 'branch_id'):
        cur.execute('ALTER TABLE "debtor_debtor" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('debtor', '0002_add_invoice_payments'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
