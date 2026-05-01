from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    cur.execute('DROP INDEX IF EXISTS "expenses_expense_branch_id_8a762cc5"')
    if has_col('expenses_expense', 'branch_id'):
        cur.execute('ALTER TABLE "expenses_expense" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0003_expense_rejection_reason_expense_status'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
