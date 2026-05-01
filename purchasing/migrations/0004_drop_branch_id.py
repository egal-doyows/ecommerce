from django.db import migrations


SQL = [
    'DROP INDEX IF EXISTS "purchasing_purchaseorder_branch_id_5ad6e832";',
    'ALTER TABLE "purchasing_purchaseorder" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0003_change_received_date_to_datetime'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
