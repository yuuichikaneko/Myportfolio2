"""GPU 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ) 窶・蜈ｨGPU蜿門ｾ・
譌ｧ: dospara 繝ｯ繝ｼ繧ｯ繧ｹ繝・・繧ｷ繝ｧ繝ｳVGA繝輔ぅ繝ｫ繧ｿ繝ｼ逕ｨ縲１C蟾･謌ｿ縺ｧ縺ｯ蜈ｨGPU繧貞叙蠕励・
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart

parts = scrape_pckoubou_category("gpu")
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

gpu_qs = PCPart.objects.filter(part_type="gpu")
print({"status": "success", "part_type": "gpu", "fetched": len(parts),
       "created": created, "updated": updated,
       "gpu_total_in_db": gpu_qs.count(),
       "sample_names": list(gpu_qs.order_by("price").values_list("name", flat=True)[:12])})
