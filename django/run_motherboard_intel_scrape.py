"""Motherboard 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ) 窶・AMD + Intel 蜈ｨ繝槭じ繝ｼ繝懊・繝牙叙蠕・
譌ｧ: dospara Intel繝槭じ繝ｼ繝懊・繝峨ヵ繧｣繝ｫ繧ｿ繝ｼ逕ｨ縲１C蟾･謌ｿ縺ｯAMD+Intel繧貞挨URL縺ｧ蜿門ｾ励・
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart

parts = scrape_pckoubou_category("motherboard")
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

mb_qs = PCPart.objects.filter(part_type="motherboard")
print({"status": "success", "part_type": "motherboard", "fetched": len(parts),
       "created": created, "updated": updated,
       "motherboard_total_in_db": mb_qs.count(),
       "sample_names": list(mb_qs.order_by("price").values_list("name", flat=True)[:10])})
