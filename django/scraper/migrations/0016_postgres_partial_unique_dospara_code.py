from django.db import migrations


INDEX_NAME = 'uq_pcpart_dospara_code_nonempty'


def _create_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    PCPart = apps.get_model('scraper', 'PCPart')
    table = schema_editor.quote_name(PCPart._meta.db_table)
    index = schema_editor.quote_name(INDEX_NAME)

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index} "
            f"ON {table} (dospara_code) "
            "WHERE dospara_code IS NOT NULL AND dospara_code <> ''"
        )


def _drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    index = schema_editor.quote_name(INDEX_NAME)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX IF EXISTS {index}")


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0015_marketpricerangesnapshot'),
    ]

    operations = [
        migrations.RunPython(_create_index, _drop_index),
    ]
