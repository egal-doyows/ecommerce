from django.db import migrations


SQL = [
    # hr_employee — drop branch_id
    'DROP INDEX IF EXISTS "hr_employee_branch_id_07bbf862";',
    'ALTER TABLE "hr_employee" DROP COLUMN "branch_id";',

    # hr_transferrequest — orphaned table (TransferRequest model removed)
    'DROP TABLE IF EXISTS "hr_transferrequest";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0003_create_profiles_for_existing_staff'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
