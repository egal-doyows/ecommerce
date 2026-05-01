from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    cur.execute('DROP INDEX IF EXISTS "expenses_expense_branch_id_8a762cc5"')
    cur.execute("SELECT 1 FROM pragma_table_info('expenses_expense') WHERE name='branch_id'")
    if cur.fetchone():
        cur.execute('ALTER TABLE "expenses_expense" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0003_expense_rejection_reason_expense_status'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
