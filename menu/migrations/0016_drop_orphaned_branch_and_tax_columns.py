from django.db import migrations


SQL = [
    # menu_order — drop branch_id + tax/order_number columns
    'DROP INDEX IF EXISTS "menu_order_branch_id_2c0e8082";',
    'DROP INDEX IF EXISTS "menu_order_branch__1e0c3f_idx";',
    'DROP INDEX IF EXISTS "menu_order_branch__e97494_idx";',
    'DROP INDEX IF EXISTS "menu_order_order_number_2300360f";',
    'ALTER TABLE "menu_order" DROP COLUMN "branch_id";',
    'ALTER TABLE "menu_order" DROP COLUMN "tax_amount";',
    'ALTER TABLE "menu_order" DROP COLUMN "tax_rate";',
    'ALTER TABLE "menu_order" DROP COLUMN "tax_type";',
    'ALTER TABLE "menu_order" DROP COLUMN "order_number";',

    # menu_orderitem — drop unused workflow columns
    'DROP INDEX IF EXISTS "menu_orderi_order_i_6ff99f_idx";',
    'ALTER TABLE "menu_orderitem" DROP COLUMN "ready_acknowledged";',
    'ALTER TABLE "menu_orderitem" DROP COLUMN "preparation_status";',

    # menu_table — drop branch_id + composite unique, restore unique(number)
    'DROP INDEX IF EXISTS "menu_table_branch_id_b4127b6e";',
    'DROP INDEX IF EXISTS "menu_table_branch_id_number_0cd481db_uniq";',
    'ALTER TABLE "menu_table" DROP COLUMN "branch_id";',
    'CREATE UNIQUE INDEX IF NOT EXISTS "menu_table_number_uniq" ON "menu_table" ("number");',

    # menu_shift — drop branch_id and its indexes
    'DROP INDEX IF EXISTS "menu_shift_branch_id_ef9208b9";',
    'DROP INDEX IF EXISTS "menu_shift_waiter__38c021_idx";',
    'DROP INDEX IF EXISTS "menu_shift_branch__f86b44_idx";',
    'ALTER TABLE "menu_shift" DROP COLUMN "branch_id";',

    # menu_inventoryitem — drop branch_id and its indexes
    'DROP INDEX IF EXISTS "menu_inventoryitem_branch_id_61563473";',
    'DROP INDEX IF EXISTS "menu_invent_branch__4a73db_idx";',
    'ALTER TABLE "menu_inventoryitem" DROP COLUMN "branch_id";',

    # menu_branchmenuavailability — orphaned table, no model
    'DROP TABLE IF EXISTS "menu_branchmenuavailability";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0015_add_created_by_to_order'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
