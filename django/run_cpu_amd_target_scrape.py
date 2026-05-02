"""CPU 繧ｹ繧ｯ繝ｬ繧､繝斐Φ繧ｰ (PC蟾･謌ｿ) 窶・AMD CPU 蜈ｨ蜿門ｾ怜ｾ後↓繝ｩ繝ｳ繧ｭ繝ｳ繧ｰ蜀咲函謌・
譌ｧ: dospara AMD繧ｿ繝ｼ繧ｲ繝・ヨ謖・ｮ夂畑縲１C蟾･謌ｿ縺ｧ縺ｯ蜈ｨCPU(Intel+AMD)繧貞叙蠕励＠繝ｩ繝ｳ繧ｭ繝ｳ繧ｰ繧貞・逕滓・縲・
"""
import os, sys
from pathlib import Path
import django

ossys_path = str(Path(__file__).parent)
if ossys_path not in sys.path:
    sys.path.insert(0, ossys_path)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart
from generate_cpu_ranking_db import generate_and_save_rankings


def main():
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
    amd_qs = cpu_qs.filter(name__icontains="ryzen").order_by("price")
    print({"status": "success", "part_type": "cpu", "fetched": len(parts),
           "created": created, "updated": updated,
           "cpu_total_in_db": cpu_qs.count(),
           "amd_total_in_db": amd_qs.count(),
           "amd_samples": list(amd_qs.values_list("name", flat=True)[:10])})

    # 蜿悶ｊ霎ｼ縺ｿ蠕後↓蜷梧擅莉ｶ縺ｮ邱丞粋繝ｩ繝ｳ繧ｭ繝ｳ繧ｰCSV繧定・蜍募・逕滓・
    generate_and_save_rankings()


if __name__ == "__main__":
    main()
