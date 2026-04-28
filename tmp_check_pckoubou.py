"""ssd/hdd → storage マッピング確認 & case_fan 確認"""
import os, sys, django
sys.path.insert(0, "django")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category

for cat in ["ssd", "hdd", "case_fan"]:
    parts = scrape_pckoubou_category(cat, max_items=3)
    print(f"\n{cat}: {len(parts)}件")
    for p in parts:
        st = p["specs"].get("storage_type", "")
        st_str = f" [storage_type={st}]" if st else ""
        print(f"  part_type={p['part_type']}{st_str} | {p['name'][:50]} | {p['price']:,}円")
