from django.db import migrations


SQL = [
    'DROP INDEX IF EXISTS "waste_wastelog_branch_id_3ca90b7f";',
    'ALTER TABLE "waste_wastelog" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('waste', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
