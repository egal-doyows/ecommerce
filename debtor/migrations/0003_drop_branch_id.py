from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "debtor_debtor_branch_id_d57f7159"')
    cur.execute("SELECT 1 FROM pragma_table_info('debtor_debtor') WHERE name='branch_id'")
    if cur.fetchone():
        cur.execute('ALTER TABLE "debtor_debtor" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('debtor', '0002_add_invoice_payments'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
