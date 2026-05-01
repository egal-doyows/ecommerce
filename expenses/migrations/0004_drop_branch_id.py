from django.db import migrations


SQL = [
    'DROP INDEX IF EXISTS "expenses_expense_branch_id_8a762cc5";',
    'ALTER TABLE "expenses_expense" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0003_expense_rejection_reason_expense_status'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
