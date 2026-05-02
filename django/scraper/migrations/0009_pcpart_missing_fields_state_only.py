from django.db import migrations, models


class Migration(migrations.Migration):
    """
    DBには既にこれらのカラムが存在するため、
    SeparateDatabaseAndState でモデル state のみ更新する。
    実際の ALTER TABLE は実行しない。
    """

    dependencies = [
        ('scraper', '0008_pcpart_chipset_state_only'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='pcpart',
                    name='currency',
                    field=models.CharField(blank=True, default='', max_length=3),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='efficiency_grade',
                    field=models.CharField(blank=True, default='', max_length=20),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='form_factor',
                    field=models.CharField(blank=True, default='', max_length=30),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='interface',
                    field=models.CharField(blank=True, default='', max_length=30),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='is_active',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='license_type',
                    field=models.CharField(blank=True, default='', max_length=30),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='maker',
                    field=models.CharField(blank=True, default='', max_length=100),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='memory_type',
                    field=models.CharField(blank=True, default='', max_length=20),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='model_code',
                    field=models.CharField(blank=True, default='', max_length=120),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='os_edition',
                    field=models.CharField(blank=True, default='', max_length=50),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='os_family',
                    field=models.CharField(blank=True, default='', max_length=30),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='socket',
                    field=models.CharField(blank=True, default='', max_length=50),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='stock_status',
                    field=models.CharField(blank=True, default='', max_length=20),
                ),
                migrations.AddField(
                    model_name='pcpart',
                    name='vram_type',
                    field=models.CharField(blank=True, default='', max_length=20),
                ),
            ],
            database_operations=[],
        ),
    ]
