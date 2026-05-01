from django.db import migrations


SQL = [
    # paymentrecord — drop branch_id
    'DROP INDEX IF EXISTS "staff_compensation_paymentrecord_branch_id_a3a7a48d";',
    'ALTER TABLE "staff_compensation_paymentrecord" DROP COLUMN "branch_id";',

    # Orphaned tables — models removed from staff_compensation/models.py
    'DROP TABLE IF EXISTS "staff_compensation_payrollline";',
    'DROP TABLE IF EXISTS "staff_compensation_payroll";',
    'DROP TABLE IF EXISTS "staff_compensation_advancerequest";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('staff_compensation', '0006_add_amount_paid_to_paymentrecord'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
