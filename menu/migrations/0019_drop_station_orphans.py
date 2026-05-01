from django.db import migrations


def forward(apps, schema_editor):
    connection = schema_editor.connection
    cur = connection.cursor()

    def has_col(table, col):
        return col in {c.name for c in connection.introspection.get_table_description(cur, table)}

    # menu_stationrequest — orphan; FK to menu_orderitem was blocking Order deletes
    cur.execute('DROP TABLE IF EXISTS "menu_stationrequest"')

    # menu_category.station_id — orphan column referencing dropped Station model
    cur.execute('DROP INDEX IF EXISTS "menu_category_station_id_e1f47a9c"')
    if has_col('menu_category', 'station_id'):
        cur.execute('ALTER TABLE "menu_category" DROP COLUMN "station_id"')

    # menu_station — orphan table (model removed)
    cur.execute('DROP TABLE IF EXISTS "menu_station"')


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0018_add_default_markup_percent'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
