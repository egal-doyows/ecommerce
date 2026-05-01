from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "receiving_goodsreceipt_branch_id_c93aaf25"')
    cur.execute("SELECT 1 FROM pragma_table_info('receiving_goodsreceipt') WHERE name='branch_id'")
    if cur.fetchone():
        cur.execute('ALTER TABLE "receiving_goodsreceipt" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('receiving', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
