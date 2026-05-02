#!/usr/bin/env python
"""
Check storage parts in database and test interface inference
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
sys.path.insert(0, '/f:/Python/Myportfolio2/django')
django.setup()

from scraper.models import PCPart
from scraper.views import _infer_storage_interface, _infer_storage_media_type

# Get all storage parts
storage_parts = PCPart.objects.filter(part_type='storage').order_by('id')

print('=== Storage Parts Analysis ===')
print(f'Total: {storage_parts.count()}')
print()

# Group by interface
interface_groups = {}
for part in storage_parts:
    interface = _infer_storage_interface(part)
    if interface not in interface_groups:
        interface_groups[interface] = []
    interface_groups[interface].append(part)

print('By Interface:')
for interface in ['nvme', 'sata', 'other']:
    parts = interface_groups.get(interface, [])
    print(f'\n{interface.upper()}: {len(parts)} items')
    for part in parts[:5]:  # Show first 5
        print(f'  - {part.name[:60]}')
        print(f'    Specs: {part.specs}')

# Find items that might be NVMe but classified as "other"
print('\n\n=== Checking "other" items for NVMe keywords ===')
other_parts = interface_groups.get('other', [])
nvme_keywords = ['nvme', 'nm', 'sn', '970', '980', '990']
nvme_in_other = []

for part in other_parts:
    text = (part.name + ' ' + (part.url or '')).lower()
    if any(keyword in text for keyword in nvme_keywords):
        nvme_in_other.append(part)
        print(f'Found: {part.name[:60]}')

print(f'\nTotal "other" items with NVMe keywords: {len(nvme_in_other)}')

# Show details on a few
if nvme_in_other:
    print('\nSample details:')
    for part in nvme_in_other[:3]:
        print(f'\nPart: {part.name}')
        print(f'  URL: {part.url}')
        print(f'  Specs: {part.specs}')
