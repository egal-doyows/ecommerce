from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "waste_wastelog_branch_id_3ca90b7f"')
    cur.execute("SELECT 1 FROM pragma_table_info('waste_wastelog') WHERE name='branch_id'")
    if cur.fetchone():
        cur.execute('ALTER TABLE "waste_wastelog" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('waste', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
