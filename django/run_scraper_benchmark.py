#!/usr/bin/env python
import os
import django
import time
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
django.setup()

from scraper.tasks import run_scraper_task
from scraper.models import ScraperStatus

print('=' * 70)
print('🚀 AUTO SCRAPER EXECUTION TEST')
print('=' * 70)
print(f'Start time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print()

# 実行時間計測開始
start_time = time.time()

try:
    # スクレイパータスク実行
    result = run_scraper_task()
    
    # 実行時間計測終了
    elapsed_time = time.time() - start_time
    
    # 結果表示
    print()
    print('=' * 70)
    print('✅ EXECUTION COMPLETED')
    print('=' * 70)
    print(f'Elapsed time: {elapsed_time:.2f} seconds ({elapsed_time/60:.2f} minutes)')
    print(f'End time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    
    # ScraperStatus 確認
    status = ScraperStatus.objects.get(id=1)
    print()
    print('📊 SCRAPER STATUS AFTER RUN')
    print('=' * 70)
    print(f'Last run: {status.last_run}')
    print(f'Next run: {status.next_run}')
    print(f'Success count: {status.success_count}')
    print(f'Total scraped: {status.total_scraped}')
    print(f'Error count: {status.error_count}')
    
except Exception as e:
    elapsed_time = time.time() - start_time
    print()
    print('=' * 70)
    print(f'❌ ERROR OCCURRED: {str(e)}')
    print('=' * 70)
    print(f'Elapsed time: {elapsed_time:.2f} seconds')
    import traceback
    traceback.print_exc()
