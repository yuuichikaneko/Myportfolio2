"""CPU の Intel/AMD 分布確認"""
import os, sys, django
sys.path.insert(0, 'django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category

cpu_parts = scrape_pckoubou_category('cpu', max_items=20)
print(f'CPU 取得件数: {len(cpu_parts)}')
intel_count = sum(1 for p in cpu_parts if 'Intel' in p['name'])
amd_count = sum(1 for p in cpu_parts if 'AMD' in p['name'] or 'Ryzen' in p['name'])
print(f'  Intel: {intel_count}件')
print(f'  AMD: {amd_count}件')
print('\n最初の15件:')
for i, p in enumerate(cpu_parts[:15], 1):
    brand = 'Intel' if 'Intel' in p['name'] else 'AMD' if 'AMD' in p['name'] or 'Ryzen' in p['name'] else '?'
    print(f'  {i:2}. [{brand:5}] {p["name"][:55]}')
