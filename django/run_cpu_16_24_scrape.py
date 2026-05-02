"""CPU 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ) 窶・Intel + AMD 蜈ｨCPU
譌ｧ: dospara 16・・4繧ｳ繧｢繝輔ぅ繝ｫ繧ｿ繝ｼ逕ｨ縲１C蟾･謌ｿ縺ｯ繧ｳ繧｢謨ｰ繝輔ぅ繝ｫ繧ｿ繝ｼ繧呈戟縺溘↑縺・◆繧∝・CPU繧貞叙蠕励・
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart

parts = scrape_pckoubou_category("cpu")
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

cpu_qs = PCPart.objects.filter(part_type="cpu")
print({"status": "success", "part_type": "cpu", "fetched": len(parts),
       "created": created, "updated": updated,
       "cpu_total_in_db": cpu_qs.count(),
       "cpu_min_price": cpu_qs.order_by("price").values_list("price", flat=True).first(),
       "cpu_max_price": cpu_qs.order_by("-price").values_list("price", flat=True).first()})
