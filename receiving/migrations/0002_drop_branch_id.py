from django.db import migrations


SQL = [
    'DROP INDEX IF EXISTS "receiving_goodsreceipt_branch_id_c93aaf25";',
    'ALTER TABLE "receiving_goodsreceipt" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('receiving', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
