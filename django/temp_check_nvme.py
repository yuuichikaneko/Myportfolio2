from scraper.models import PCPart
from scraper.views import _infer_storage_interface, _infer_storage_media_type

# Check for NVMe-like names in 'other' category
other_nvme = []
for p in PCPart.objects.filter(part_type='storage'):
    if _infer_storage_interface(p) == 'other':
        name_lower = (p.name + ' ' + (p.url or '')).lower()
        if any(kw in name_lower for kw in ['nvme', 'nm', 'sn85', 'sn70', 'sn75', '970', '980', '990', '2280', '2242']):
            media = _infer_storage_media_type(p)
            other_nvme.append((p.id, p.name[:50], media))

print(f'Found {len(other_nvme)} NVMe-like items in "other" interface category:')
for pid, pname, media in other_nvme[:15]:
    print(f'  ID {pid}: {pname} (media: {media})')
