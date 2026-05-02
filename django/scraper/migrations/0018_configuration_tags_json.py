import json

from django.db import migrations, models


def _split_tags(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [segment.strip() for segment in value.split(',') if segment.strip()]
    return []


def forwards(apps, schema_editor):
    Configuration = apps.get_model('scraper', 'Configuration')
    for config in Configuration.objects.all().iterator():
        # CharField 段階で JSON 文字列へ正規化してから JSONField へ変換する
        config.tags = json.dumps(_split_tags(config.tags), ensure_ascii=False)
        config.save(update_fields=['tags'])


def backwards(apps, schema_editor):
    Configuration = apps.get_model('scraper', 'Configuration')
    for config in Configuration.objects.all().iterator():
        if isinstance(config.tags, list):
            config.tags = ', '.join([str(item).strip() for item in config.tags if str(item).strip()])
            config.save(update_fields=['tags'])


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0017_configuration_memo_tags'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name='configuration',
            name='tags',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
