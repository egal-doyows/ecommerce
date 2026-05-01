from django.db import migrations


def forward(apps, schema_editor):
    """
    Drop drift left over from the multi-branch revert. Idempotent: each
    operation checks for existence so the migration is a no-op against a
    fresh DB where the drift never occurred.
    """
    cur = schema_editor.connection.cursor()

    def has_col(table, col):
        cur.execute(f"SELECT 1 FROM pragma_table_info('{table}') WHERE name=?", [col])
        return cur.fetchone() is not None

    # DROP INDEX/TABLE IF EXISTS are natively idempotent.
    for sql in [
        'DROP INDEX IF EXISTS "menu_order_branch_id_2c0e8082"',
        'DROP INDEX IF EXISTS "menu_order_branch__1e0c3f_idx"',
        'DROP INDEX IF EXISTS "menu_order_branch__e97494_idx"',
        'DROP INDEX IF EXISTS "menu_order_order_number_2300360f"',
        'DROP INDEX IF EXISTS "menu_orderi_order_i_6ff99f_idx"',
        'DROP INDEX IF EXISTS "menu_table_branch_id_b4127b6e"',
        'DROP INDEX IF EXISTS "menu_table_branch_id_number_0cd481db_uniq"',
        'DROP INDEX IF EXISTS "menu_shift_branch_id_ef9208b9"',
        'DROP INDEX IF EXISTS "menu_shift_waiter__38c021_idx"',
        'DROP INDEX IF EXISTS "menu_shift_branch__f86b44_idx"',
        'DROP INDEX IF EXISTS "menu_inventoryitem_branch_id_61563473"',
        'DROP INDEX IF EXISTS "menu_invent_branch__4a73db_idx"',
        'DROP TABLE IF EXISTS "menu_branchmenuavailability"',
    ]:
        cur.execute(sql)

    drops = [
        ('menu_order', 'branch_id'),
        ('menu_order', 'tax_amount'),
        ('menu_order', 'tax_rate'),
        ('menu_order', 'tax_type'),
        ('menu_order', 'order_number'),
        ('menu_orderitem', 'ready_acknowledged'),
        ('menu_orderitem', 'preparation_status'),
        ('menu_table', 'branch_id'),
        ('menu_shift', 'branch_id'),
        ('menu_inventoryitem', 'branch_id'),
    ]
    for table, col in drops:
        if has_col(table, col):
            cur.execute(f'ALTER TABLE "{table}" DROP COLUMN "{col}"')

    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS "menu_table_number_uniq" ON "menu_table" ("number")')


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0015_add_created_by_to_order'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
