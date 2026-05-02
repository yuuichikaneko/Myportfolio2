from django.db import migrations


def create_performance_indexes(apps, schema_editor):
    vendor = schema_editor.connection.vendor

    if vendor == 'postgresql':
        statements = [
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pcpart_type_price_name ON scraper_pcpart (part_type, price, name);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_configuration_is_deleted_created_at ON scraper_configuration (is_deleted, created_at DESC);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scraperstatus_updated_at_desc ON scraper_scraperstatus (updated_at DESC);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_configuration_usage_is_deleted_created_at ON scraper_configuration (usage, is_deleted, created_at DESC);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pcpart_storage_capacity_price ON scraper_pcpart (capacity_gb, price) WHERE part_type = 'storage';",
            "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pcpart_name_trgm ON scraper_pcpart USING GIN (name gin_trgm_ops);",
            (
                "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_pcpart_dospara_code_not_blank "
                "ON scraper_pcpart (dospara_code) "
                "WHERE dospara_code IS NOT NULL AND dospara_code <> '';"
            ),
        ]
    elif vendor == 'sqlite':
        statements = [
            "CREATE INDEX IF NOT EXISTS idx_pcpart_type_price_name ON scraper_pcpart (part_type, price, name);",
            "CREATE INDEX IF NOT EXISTS idx_configuration_is_deleted_created_at ON scraper_configuration (is_deleted, created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_scraperstatus_updated_at_desc ON scraper_scraperstatus (updated_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_configuration_usage_is_deleted_created_at ON scraper_configuration (usage, is_deleted, created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_pcpart_storage_capacity_price ON scraper_pcpart (capacity_gb, price) WHERE part_type = 'storage';",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_pcpart_dospara_code_not_blank "
                "ON scraper_pcpart (dospara_code) "
                "WHERE dospara_code IS NOT NULL AND dospara_code <> '';"
            ),
        ]
    else:
        statements = []

    for statement in statements:
        schema_editor.execute(statement)


def drop_performance_indexes(apps, schema_editor):
    vendor = schema_editor.connection.vendor

    if vendor == 'postgresql':
        statements = [
            "DROP INDEX CONCURRENTLY IF EXISTS idx_pcpart_type_price_name;",
            "DROP INDEX CONCURRENTLY IF EXISTS idx_configuration_is_deleted_created_at;",
            "DROP INDEX CONCURRENTLY IF EXISTS idx_scraperstatus_updated_at_desc;",
            "DROP INDEX CONCURRENTLY IF EXISTS idx_configuration_usage_is_deleted_created_at;",
            "DROP INDEX CONCURRENTLY IF EXISTS idx_pcpart_storage_capacity_price;",
            "DROP INDEX CONCURRENTLY IF EXISTS idx_pcpart_name_trgm;",
            "DROP INDEX CONCURRENTLY IF EXISTS uq_pcpart_dospara_code_not_blank;",
        ]
    elif vendor == 'sqlite':
        statements = [
            "DROP INDEX IF EXISTS idx_pcpart_type_price_name;",
            "DROP INDEX IF EXISTS idx_configuration_is_deleted_created_at;",
            "DROP INDEX IF EXISTS idx_scraperstatus_updated_at_desc;",
            "DROP INDEX IF EXISTS idx_configuration_usage_is_deleted_created_at;",
            "DROP INDEX IF EXISTS idx_pcpart_storage_capacity_price;",
            "DROP INDEX IF EXISTS uq_pcpart_dospara_code_not_blank;",
        ]
    else:
        statements = []

    for statement in statements:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('scraper', '0011_backfill_3nf_reference_masters'),
    ]

    operations = [
        migrations.RunPython(create_performance_indexes, drop_performance_indexes),
    ]
