from django.db import migrations


SQL = [
    'DROP INDEX IF EXISTS "supplier_suppliertransaction_branch_id_d5acb92c";',
    'ALTER TABLE "supplier_suppliertransaction" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('supplier', '0002_add_payment_allocation'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
