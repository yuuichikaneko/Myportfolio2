"""
Management command to backfill StorageDetail.storage_category from existing parts.

Usage:
    python manage.py populate_storage_category
"""

from django.core.management.base import BaseCommand

from scraper.models import PCPart, StorageDetail
from scraper.views import _infer_storage_interface


class Command(BaseCommand):
    help = 'Backfill StorageDetail.storage_category as nvme/sata/other'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting populate_storage_category...'))

        total = 0
        updated = 0
        distribution = {'nvme': 0, 'sata': 0, 'other': 0}

        storage_parts = PCPart.objects.filter(part_type='storage')

        for part in storage_parts:
            total += 1
            category = _infer_storage_interface(part)
            if category not in distribution:
                category = 'other'

            distribution[category] += 1

            storage_detail, _ = StorageDetail.objects.get_or_create(part=part)
            if storage_detail.storage_category != category:
                storage_detail.storage_category = category
                storage_detail.save(update_fields=['storage_category'])
                updated += 1

        self.stdout.write(self.style.SUCCESS('\n=== Summary ==='))
        self.stdout.write(f'Total storage parts: {total}')
        self.stdout.write(f'Updated storage_category: {updated}')
        self.stdout.write('')
        self.stdout.write('Storage category distribution:')
        self.stdout.write(f'  NVMe: {distribution["nvme"]}')
        self.stdout.write(f'  SATA: {distribution["sata"]}')
        self.stdout.write(f'  Other: {distribution["other"]}')
        self.stdout.write(self.style.SUCCESS('\nDone!'))
