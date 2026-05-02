"""Memory 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ)
譌ｧ: dospara SBR1017 繝｡繝｢繝ｪ繧ｫ繝・ざ繝ｪ繝ｼ逕ｨ縲・
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart

parts = scrape_pckoubou_category("memory")
created = updated = 0
for p in parts:
    _, is_created = PCPart.objects.update_or_create(
        part_type=p["part_type"], name=p["name"],
        defaults={"price": p["price"], "url": p["url"],
                  "specs": p.get("specs", {"source": "pckoubou"}),
                  "stock_status": p.get("stock_status", "unknown"), "is_active": True},
    )
    if is_created: created += 1
    else: updated += 1

mem_qs = PCPart.objects.filter(part_type="memory")
print({"status": "success", "part_type": "memory", "fetched": len(parts),
       "created": created, "updated": updated,
       "memory_total_in_db": mem_qs.count(),
       "db_total_parts": PCPart.objects.count()})
