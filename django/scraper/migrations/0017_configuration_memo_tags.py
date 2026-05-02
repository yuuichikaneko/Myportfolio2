from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0016_postgres_partial_unique_dospara_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuration',
            name='memo',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='configuration',
            name='tags',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
