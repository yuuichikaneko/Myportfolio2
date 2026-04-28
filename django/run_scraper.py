import os
import sys
import django
import logging

# run_scraperの大量書き込み中にstartup初期化スレッドが同時書き込みして
# SQLiteロック競合を起こすのを防ぐ。
os.environ.setdefault('DJANGO_SKIP_SCRAPER_STARTUP_INIT', '1')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

logging.basicConfig(level=logging.INFO)

from scraper.pckoubou_scraper import scrape_pckoubou_all
from scraper.models import PCPart, ScraperStatus
from django.utils import timezone
from django.db.models import Count

print("=" * 60)
print("スクレイピング開始（PC工房）...")
print("=" * 60)

try:
    scraped_parts = scrape_pckoubou_all(max_items_per_category=500)
    print(f"\n[OK] {len(scraped_parts)} 個のパーツを取得しました。")

    saved_count = 0
    updated_count = 0

    for part in scraped_parts:
        obj, created = PCPart.objects.update_or_create(
            part_type=part['part_type'],
            name=part['name'],
            defaults={
                'price': part['price'],
                'url': part['url'],
                'specs': part.get('specs', {'source': 'pckoubou'}),
                'stock_status': part.get('stock_status', 'unknown'),
                'is_active': True,
            }
        )
        if created:
            saved_count += 1
        else:
            updated_count += 1

    status, _ = ScraperStatus.objects.get_or_create(id=1)
    status.last_run = timezone.now()
    status.total_scraped = len(scraped_parts)
    status.success_count = (status.success_count or 0) + 1
    status.save()

    print(f"[OK] 新規保存: {saved_count} 個")
    print(f"[OK] 更新: {updated_count} 個")
    print(f"\n現在のDB: 全 {PCPart.objects.count()} 個のパーツ")

    counts = PCPart.objects.values('part_type').annotate(cnt=Count('id')).order_by('part_type')
    for row in counts:
        print(f"  {row['part_type']}: {row['cnt']}個")

    print("\n" + "=" * 60)
    print("スクレイピング完了！")
    print("=" * 60)

except Exception as e:
    import traceback
    print(f"[エラー] {e}")
    traceback.print_exc()
