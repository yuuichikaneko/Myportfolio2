from django.apps import AppConfig
import os
import sys


class ScraperConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scraper'

    def ready(self):
        """Django起動時にスナップショット初期化タスクを実行"""
        # 管理コマンド実行中は重い初期化を抑止して、migrate系の安定性を優先する。
        command = (sys.argv[1] if len(sys.argv) > 1 else '').lower()
        skip_commands = {
            'migrate',
            'makemigrations',
            'showmigrations',
            'sqlmigrate',
            'collectstatic',
            'check',
            'test',
            'shell',
            'dbshell',
            'createsuperuser',
            'loaddata',
            'dumpdata',
            'flush',
        }
        if os.environ.get('DJANGO_SKIP_SCRAPER_STARTUP_INIT', '').strip().lower() in {'1', 'true', 'yes'}:
            return
        if command in skip_commands:
            return

        import threading
        from .tasks import (
            import_market_price_range_task,
            import_cpu_selection_material_task,
            import_gpu_performance_scores_task,
        )
        
        def initialize_snapshots():
            """バックグラウンドでスナップショットを初期化"""
            try:
                # 最新スナップショットが存在しするか確認
                from .models import MarketPriceRangeSnapshot, CPUSelectionSnapshot, GPUPerformanceSnapshot
                from .views import _load_latest_cpu_selection_scores, _load_latest_gpu_perf_scores
                
                market_exists = MarketPriceRangeSnapshot.objects.exists()
                cpu_exists = CPUSelectionSnapshot.objects.exists()
                gpu_exists = GPUPerformanceSnapshot.objects.exists()
                
                # いずれか1つでも存在しなければ初期化を実行
                if not market_exists or not cpu_exists or not gpu_exists:
                    print("[Scraper App Startup] Initializing snapshot data...")
                    if not market_exists:
                        print(" - Importing market price range...")
                        import_market_price_range_task(timeout=20)
                    if not cpu_exists:
                        print(" - Importing CPU selection material...")
                        import_cpu_selection_material_task(timeout=20)
                    if not gpu_exists:
                        print(" - Importing GPU performance scores...")
                        import_gpu_performance_scores_task(timeout=20)
                    print("[Scraper App Startup] Initialization complete.")

                # 起動直後の初回選定でスナップショット読み込みスパイクを避ける。
                _load_latest_cpu_selection_scores()
                _load_latest_gpu_perf_scores()
            except Exception as e:
                print(f"[Scraper App Startup] Initialization error: {e}")
        
        # バックグラウンドスレッドで実行（Django起動をブロックしない）
        init_thread = threading.Thread(target=initialize_snapshots, daemon=True)
        init_thread.start()

