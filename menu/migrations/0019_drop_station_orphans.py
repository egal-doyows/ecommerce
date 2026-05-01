from django.db import migrations


def forward(apps, schema_editor):
    cur = schema_editor.connection.cursor()
    # menu_stationrequest — orphan; FK to menu_orderitem was blocking Order deletes
    cur.execute('DROP TABLE IF EXISTS "menu_stationrequest"')

    # menu_category.station_id — orphan column referencing dropped Station model
    cur.execute('DROP INDEX IF EXISTS "menu_category_station_id_e1f47a9c"')
    cur.execute("SELECT 1 FROM pragma_table_info('menu_category') WHERE name='station_id'")
    if cur.fetchone():
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
