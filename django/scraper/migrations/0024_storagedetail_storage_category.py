from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0023_add_case_fan_to_configuration'),
    ]

    operations = [
        migrations.AddField(
            model_name='storagedetail',
            name='storage_category',
            field=models.CharField(blank=True, db_index=True, max_length=10),
        ),
    ]
