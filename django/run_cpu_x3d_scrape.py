"""CPU 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ) 窶・Intel + AMD 蜈ｨCPU
譌ｧ: dospara X3D繝輔ぅ繝ｫ繧ｿ繝ｼ逕ｨ縲１C蟾･謌ｿ縺ｫ蝠・刀蜷阪ヵ繧｣繝ｫ繧ｿ繝ｼ縺ｯ縺ｪ縺・◆繧∝・CPU繧貞叙蠕励．B蜀・〒X3D繧呈､懃ｴ｢縲・
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
x3d_qs = cpu_qs.filter(name__icontains="x3d").order_by("price")
print({"status": "success", "part_type": "cpu", "fetched": len(parts),
       "created": created, "updated": updated,
       "cpu_total_in_db": cpu_qs.count(),
       "x3d_total_in_db": x3d_qs.count(),
       "x3d_samples": list(x3d_qs.values_list("name", flat=True)[:10])})
