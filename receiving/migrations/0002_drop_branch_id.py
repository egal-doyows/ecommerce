from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    cur.execute('DROP INDEX IF EXISTS "receiving_goodsreceipt_branch_id_c93aaf25"')
    if has_col('receiving_goodsreceipt', 'branch_id'):
        cur.execute('ALTER TABLE "receiving_goodsreceipt" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('receiving', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
