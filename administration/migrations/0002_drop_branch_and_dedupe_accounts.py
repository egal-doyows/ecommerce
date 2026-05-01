from django.db import migrations


SQL = [
    # Repoint every transaction to the canonical account (lowest id per type)
    """
    UPDATE administration_transaction
    SET account_id = (
        SELECT MIN(a2.id)
        FROM administration_account a2
        WHERE a2.account_type = (
            SELECT a1.account_type
            FROM administration_account a1
            WHERE a1.id = administration_transaction.account_id
        )
    );
    """,
    # Delete the non-canonical duplicates
    """
    DELETE FROM administration_account
    WHERE id NOT IN (
        SELECT MIN(id) FROM administration_account GROUP BY account_type
    );
    """,
    # administration_account: drop branch_id + composite unique, add unique(account_type)
    'DROP INDEX IF EXISTS "administration_account_branch_id_142f8570";',
    'DROP INDEX IF EXISTS "administration_account_branch_id_account_type_2c6f58cc_uniq";',
    'ALTER TABLE "administration_account" DROP COLUMN "branch_id";',
    'CREATE UNIQUE INDEX IF NOT EXISTS "administration_account_account_type_uniq" ON "administration_account" ("account_type");',

    # administration_transaction: drop branch_id and its indexes
    'DROP INDEX IF EXISTS "administration_transaction_branch_id_ed8fc06b";',
    'DROP INDEX IF EXISTS "administrat_branch__10536f_idx";',
    'ALTER TABLE "administration_transaction" DROP COLUMN "branch_id";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
