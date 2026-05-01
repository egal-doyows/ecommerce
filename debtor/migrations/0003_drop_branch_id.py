from django.db import migrations


SQL = [
    'DROP INDEX IF EXISTS "debtor_debtor_branch_id_d57f7159";',
    'ALTER TABLE "debtor_debtor" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('debtor', '0002_add_invoice_payments'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
