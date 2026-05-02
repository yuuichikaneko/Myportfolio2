from django.db import migrations, models


class Migration(migrations.Migration):
    """
    chipset カラムはすでに DB に存在するため、
    SeparateDatabaseAndState で Django の model state のみ更新する。
    実際の ALTER TABLE は実行しない。
    """

    dependencies = [
        ('scraper', '0007_configuration_name_field'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='pcpart',
                    name='chipset',
                    field=models.CharField(blank=True, default='', max_length=50),
                ),
            ],
            database_operations=[],
        ),
    ]
