from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "hr_employee_branch_id_07bbf862"')
    cur.execute("SELECT 1 FROM pragma_table_info('hr_employee') WHERE name='branch_id'")
    if cur.fetchone():
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
