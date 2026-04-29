"""
Management command to backfill StorageDetail with interface and form_factor information.

Usage:
    python manage.py populate_storage_details
"""

from django.core.management.base import BaseCommand
from scraper.models import PCPart, StorageDetail
from scraper.views import _infer_storage_interface, _infer_storage_media_type


class Command(BaseCommand):
    help = 'Backfill StorageDetail with interface and form_factor information from PCPart specs'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting populate_storage_details...'))

        # Counters
        total = 0
        updated = 0
        interface_dist = {'nvme': 0, 'sata': 0, 'other': 0}
        media_dist = {'ssd': 0, 'hdd': 0, 'other': 0}

        # Get all storage parts
        storage_parts = PCPart.objects.filter(part_type='storage')

        for part in storage_parts:
            total += 1

            # Infer interface and media type
            interface = _infer_storage_interface(part)
            media_type = _infer_storage_media_type(part)

            # Update distributions
            interface_dist[interface] = interface_dist.get(interface, 0) + 1
            media_dist[media_type] = media_dist.get(media_type, 0) + 1

            # Get or create StorageDetail
            storage_detail, created = StorageDetail.objects.get_or_create(part=part)

            # Always update interface and form_factor (force update)
            changed = False

            # Update interface
            new_interface = interface
            if storage_detail.interface != new_interface:
                storage_detail.interface = new_interface
                changed = True

            # Keep storage_category aligned with inferred interface
            if hasattr(storage_detail, 'storage_category') and storage_detail.storage_category != interface:
                storage_detail.storage_category = interface
                changed = True

            # Extract form_factor from specs if available
            if (not storage_detail.form_factor or storage_detail.form_factor == 'unknown') and hasattr(part, 'specs') and part.specs:
                specs = part.specs
                if isinstance(specs, dict):
                    # Try to find form_factor in specs
                    for key in ['form_factor', 'Form Factor', 'formFactor', 'size', 'Size']:
                        if key in specs:
                            form_factor = specs[key]
                            if storage_detail.form_factor != form_factor:
                                storage_detail.form_factor = form_factor
                                changed = True
                            break

            # Save if changed
            if changed:
                storage_detail.save()
                updated += 1
                self.stdout.write(
                    f"Updated {part.id}: {part.name[:50]} - "
                    f"interface={interface}, media={media_type}"
                )

        # Summary
        self.stdout.write(self.style.SUCCESS('\n=== Summary ==='))
        self.stdout.write(f'Total storage parts: {total}')
        self.stdout.write(f'Updated details: {updated}')
        self.stdout.write(f'\nInterface distribution:')
        self.stdout.write(f'  NVMe: {interface_dist["nvme"]}')
        self.stdout.write(f'  SATA: {interface_dist["sata"]}')
        self.stdout.write(f'  Other: {interface_dist["other"]}')
        self.stdout.write(f'\nMedia distribution:')
        self.stdout.write(f'  SSD: {media_dist["ssd"]}')
        self.stdout.write(f'  HDD: {media_dist["hdd"]}')
        self.stdout.write(f'  Other: {media_dist["other"]}')
        self.stdout.write(self.style.SUCCESS('\nDone!'))
