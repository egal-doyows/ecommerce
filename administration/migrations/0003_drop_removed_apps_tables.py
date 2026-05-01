from django.db import migrations


SQL = [
    # branches app removed
    'DROP TABLE IF EXISTS "branches_userbranch";',
    'DROP TABLE IF EXISTS "branches_branch";',

    # tax app removed
    'DROP TABLE IF EXISTS "tax_taxconfiguration";',

    # assets app removed
    'DROP TABLE IF EXISTS "assets_asset";',
    'DROP TABLE IF EXISTS "assets_assetcategory";',

    # stocks app removed
    'DROP TABLE IF EXISTS "stocks_stockadjustmentline";',
    'DROP TABLE IF EXISTS "stocks_stockadjustment";',
    'DROP TABLE IF EXISTS "stocks_stockmovement";',

    # Clean up django_migrations rows so showmigrations doesn't list ghosts
    "DELETE FROM django_migrations WHERE app IN ('branches', 'tax', 'assets', 'stocks');",
]


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0002_drop_branch_and_dedupe_accounts'),
        # Must run after every per-app drop of branch_id, otherwise the FK
        # check at the end of those migrations sees dangling references to
        # branches_branch.
        ('debtor', '0003_drop_branch_id'),
        ('expenses', '0004_drop_branch_id'),
        ('hr', '0004_drop_branch_drift'),
        ('purchasing', '0004_drop_branch_id'),
        ('receiving', '0002_drop_branch_id'),
        ('supplier', '0003_drop_branch_id'),
        ('waste', '0002_drop_branch_id'),
        ('staff_compensation', '0007_drop_branch_drift'),
        ('menu', '0016_drop_orphaned_branch_and_tax_columns'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
