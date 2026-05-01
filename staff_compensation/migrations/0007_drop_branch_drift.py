from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    cur.execute('DROP INDEX IF EXISTS "staff_compensation_paymentrecord_branch_id_a3a7a48d"')
    if has_col('staff_compensation_paymentrecord', 'branch_id'):
        cur.execute('ALTER TABLE "staff_compensation_paymentrecord" DROP COLUMN "branch_id"')

    # Orphaned tables — AdvanceRequest, Payroll, PayrollLine models removed.
    cur.execute('DROP TABLE IF EXISTS "staff_compensation_payrollline"')
    cur.execute('DROP TABLE IF EXISTS "staff_compensation_payroll"')
    cur.execute('DROP TABLE IF EXISTS "staff_compensation_advancerequest"')


class Migration(migrations.Migration):

    dependencies = [
        ('staff_compensation', '0006_add_amount_paid_to_paymentrecord'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
