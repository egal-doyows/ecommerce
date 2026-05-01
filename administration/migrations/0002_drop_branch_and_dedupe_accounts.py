from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    # Repoint every transaction to the canonical account (lowest id per type).
    # No-op when there are no rows / no duplicates (fresh DB).
    cur.execute("""
        UPDATE administration_transaction
        SET account_id = (
            SELECT MIN(a2.id)
            FROM administration_account a2
            WHERE a2.account_type = (
                SELECT a1.account_type
                FROM administration_account a1
                WHERE a1.id = administration_transaction.account_id
            )
        )
    """)
    cur.execute("""
        DELETE FROM administration_account
        WHERE id NOT IN (
            SELECT MIN(id) FROM administration_account GROUP BY account_type
        )
    """)

    cur.execute('DROP INDEX IF EXISTS "administration_account_branch_id_142f8570"')
    cur.execute('DROP INDEX IF EXISTS "administration_account_branch_id_account_type_2c6f58cc_uniq"')
    if has_col('administration_account', 'branch_id'):
        cur.execute('ALTER TABLE "administration_account" DROP COLUMN "branch_id"')
    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS "administration_account_account_type_uniq" ON "administration_account" ("account_type")')

    cur.execute('DROP INDEX IF EXISTS "administration_transaction_branch_id_ed8fc06b"')
    cur.execute('DROP INDEX IF EXISTS "administrat_branch__10536f_idx"')
    if has_col('administration_transaction', 'branch_id'):
        cur.execute('ALTER TABLE "administration_transaction" DROP COLUMN "branch_id"')


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
