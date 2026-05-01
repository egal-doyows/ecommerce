from django.db import migrations


SQL = [
    # menu_stationrequest — orphan table; its FK to menu_orderitem was blocking
    # Order deletes via the admin (Django ORM didn't know to cascade).
    'DROP TABLE IF EXISTS "menu_stationrequest";',

    # menu_category.station_id — orphan column referencing dropped Station model
    'DROP INDEX IF EXISTS "menu_category_station_id_e1f47a9c";',
    'ALTER TABLE "menu_category" DROP COLUMN "station_id";',

    # menu_station — orphan table (model removed)
    'DROP TABLE IF EXISTS "menu_station";',
]


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0018_add_default_markup_percent'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
