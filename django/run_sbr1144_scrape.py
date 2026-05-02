"""Storage (SSD + HDD) 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ)
譌ｧ: dospara SBR1144 繧ｹ繝医Ξ繝ｼ繧ｸ繧ｫ繝・ざ繝ｪ繝ｼ逕ｨ縲・
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart

created = updated = 0
all_parts = []

for cat in ("ssd", "hdd", "hdd25"):
    parts = scrape_pckoubou_category(cat)
    all_parts.extend(parts)
    for p in parts:
        _, is_created = PCPart.objects.update_or_create(
            part_type=p["part_type"], name=p["name"],
            defaults={"price": p["price"], "url": p["url"],
                      "specs": p.get("specs", {"source": "pckoubou"}),
                      "stock_status": p.get("stock_status", "unknown"), "is_active": True},
        )
        if is_created: created += 1
        else: updated += 1

storage_qs = PCPart.objects.filter(part_type="storage")
part_types = sorted({p.get("part_type") for p in all_parts if p.get("part_type")})
print({"status": "success", "categories": ["ssd", "hdd", "hdd25"], "fetched": len(all_parts),
       "created": created, "updated": updated,
       "part_types": part_types,
       "storage_total_in_db": storage_qs.count()})
