from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0014_cpuselectionsnapshot_cpuselectionentry'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketPriceRangeSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_name', models.CharField(db_index=True, default='dospara_tc30_market', max_length=80)),
                ('market_min', models.IntegerField()),
                ('market_max', models.IntegerField()),
                ('suggested_default', models.IntegerField()),
                ('currency', models.CharField(default='JPY', max_length=3)),
                ('sources', models.JSONField(blank=True, default=dict)),
                ('fetched_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['-fetched_at'],
            },
        ),
    ]
