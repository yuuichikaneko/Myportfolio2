from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0018_configuration_tags_json'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='configuration',
            name='memo',
        ),
        migrations.RemoveField(
            model_name='configuration',
            name='tags',
        ),
    ]
