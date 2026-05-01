from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    cur.execute('DROP INDEX IF EXISTS "hr_employee_branch_id_07bbf862"')
    if has_col('hr_employee', 'branch_id'):
        cur.execute('ALTER TABLE "hr_employee" DROP COLUMN "branch_id"')
    # hr_transferrequest — orphaned table (TransferRequest model removed)
    cur.execute('DROP TABLE IF EXISTS "hr_transferrequest"')


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0003_create_profiles_for_existing_staff'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
