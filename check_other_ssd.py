import os, sys, django
sys.path.insert(0, '/f:/Python/Myportfolio2/django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
django.setup()

from scraper.models import PCPart
from scraper.views import _infer_storage_interface, _infer_storage_media_type

# Get samples from 'other' category
other_samples = []
for p in PCPart.objects.filter(part_type='storage'):
    if _infer_storage_interface(p) == 'other':
        media = _infer_storage_media_type(p)
        if media == 'ssd':  # Focus on SSDs
            other_samples.append((p.id, p.name, p.specs))
            if len(other_samples) >= 5:
                break

print('Sample SSD items in "other" category:')
for pid, pname, specs in other_samples:
    print(f'\nID {pid}: {pname[:60]}')
    print(f'  Specs: {specs}')
