from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0005_configuration_storage2_storage3'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuration',
            name='usage',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('gaming', 'Gaming'),
                    ('creator', 'Creator'),
                    ('business', 'Business'),
                    ('standard', 'Standard'),
                    ('video_editing', 'Video Editing'),
                    ('general', 'General'),
                ],
            ),
        ),
    ]
