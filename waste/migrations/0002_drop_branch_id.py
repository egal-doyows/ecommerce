from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "waste_wastelog_branch_id_3ca90b7f"')
    columns = {c.name for c in connection.introspection.get_table_description(cur, 'waste_wastelog')}
    if 'branch_id' in columns:
        cur.execute('ALTER TABLE "waste_wastelog" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('waste', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
