# Flask + Celery 移行チェックリスト（Django 併用）

**現状:** Django 4.2.11 + Celery 5.3.6 + SQLite  
**目標:** Flask で新規タスク定義しながら Django と共存  
**所要時間:** 約 30-60 分（Redis インストール除く）

---

## フェーズ 1: 前提条件確認

### [ ] 1-1. Redis インストール確認
```powershell
# Windows: Chocolatey でのインストール（管理者権限で実行）
choco install redis-64 -y

# または WSL2/Docker で Redis を起動
```

### [ ] 1-2. Redis 接続テスト
```powershell
# PowerShell で実行
$ok=$false; $c=$null
try { $c=[System.Net.Sockets.TcpClient]::new('127.0.0.1',6379); $ok=$c.Connected } 
catch {} 
finally { if($c){$c.Dispose()} }
if($ok){'✓ Redis: OPEN'} else {'✗ Redis: CLOSED - インストール必須'}
```

### [ ] 1-3. Django 環境確認
```powershell
cd f:\Python\Myportfolio
.\.venv\Scripts\Activate.ps1
python -c "import celery; print(f'✓ Celery {celery.__version__}')"
python manage.py showmigrations scraper | grep -c "\[X\]"  # すべて [X] なら OK
```

### [ ] 1-4. 既存タスク一覧を確認
```powershell
# Django shell で現在のタスク確認
python -c "
from django.conf import settings
import django
django.setup()
from scraper.tasks import run_scraper_task
print('✓ Existing task: run_scraper_task')
"
```

---

## フェーズ 2: Flask Celery インスタンス作成

### [ ] 2-1. `flask_service/` ディレクトリ作成
```powershell
mkdir flask_service
cd flask_service
```

### [ ] 2-2. `celery_app.py` を作成（Flask 用 Celery インスタンス）
```powershell
# ファイル: flask_service/celery_app.py
```

**コピペして作成:**
```python
from celery import Celery

def make_celery():
    app = Celery(
        'myportfolio_flask',
        broker='redis://127.0.0.1:6379/0',
        backend='redis://127.0.0.1:6379/1',
        include=['flask_service.tasks']
    )
    
    app.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Tokyo',
        enable_utc=True,
    )
    
    return app

celery_app = make_celery()
```

### [ ] 2-3. `__init__.py` を作成（Flask アプリ初期化）
```python
# ファイル: flask_service/__init__.py
from flask import Flask
from .celery_app import celery_app

def create_app():
    app = Flask(__name__)
    celery_app.conf.update(app.config)
    return app
```

### [ ] 2-4. `tasks.py` を作成（Flask 側タスク定義）
```python
# ファイル: flask_service/tasks.py
from .celery_app import celery_app

@celery_app.task(name='flask_service.test_task')
def test_task(message):
    """Flask 側でのテストタスク"""
    return f"Flask Task executed: {message}"

@celery_app.task(name='flask_service.run_scraper_task_v2')
def run_scraper_task_v2(part_type, source='dospara'):
    """
    Flask 側での scraper タスク（Django ORM と共有）
    
    part_type: 'cpu', 'gpu', 'motherboard' など
    source: 'dospara'
    """
    import django
    django.setup()  # Django ORM 初期化
    
    from scraper.tasks import run_scraper_task
    # Django 側のタスク実行ロジックを呼び出す
    return run_scraper_task(part_type=part_type, source=source)
```

---

## フェーズ 3: Celery ブローカー設定確認（Django 側）

### [ ] 3-1. Django Celery 設定を確認
```powershell
# ファイル: django/myportfolio_django/celery.py の内容確認
python -c "
import django
django.setup()
import os
from django.conf import settings
print('Broker URL:', settings.CELERY_BROKER_URL if hasattr(settings, 'CELERY_BROKER_URL') else 'NOT SET')
"
```

### [ ] 3-2. Django 側の Celery が Redis を正しく指す設定
```python
# django/myportfolio_django/celery.py： 以下のような設定があること
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')

app = Celery('myportfolio')
app.config_from_object('django.conf:settings', namespace='CELERY')

# Redis 設定
app.conf.broker_url = 'redis://127.0.0.1:6379/0'
app.conf.result_backend = 'redis://127.0.0.1:6379/1'
```

---

## フェーズ 4: 並行実行テスト

### [ ] 4-1. Django Celery Worker を起動（ターミナル 1）
```powershell
cd f:\Python\Myportfolio
.\.venv\Scripts\Activate.ps1
python manage.py celery -A myportfolio_django.celery worker -l info -P solo
```

### [ ] 4-2. Flask Celery Worker を起動（ターミナル 2）
```powershell
cd f:\Python\Myportfolio
.\.venv\Scripts\Activate.ps1
python -m celery -A flask_service.celery_app worker -l info -P solo
```

### [ ] 4-3. Redis 監視（ターミナル 3）
```powershell
# Redis CLI で接続（Redis がインストールされている場合）
redis-cli
> MONITOR  # すべてのコマンド表示
```

---

## フェーズ 5: テストタスク実行

### [ ] 5-1. Django タスク実行テスト
```powershell
# ターミナル 4：Django shell
cd f:\Python\Myportfolio
.\.venv\Scripts\Activate.ps1
python manage.py shell
```

```python
# Django shell 内で実行
from scraper.tasks import run_scraper_task
task = run_scraper_task.delay(part_type='case')
print(f"Task ID: {task.id}")
print(f"Status: {task.status}")
```

### [ ] 5-2. Flask タスク実行テスト
```powershell
# ターミナル 4 で続ける（または新規）
cd f:\Python\Myportfolio
.\.venv\Scripts\Activate.ps1
python -c "
from flask_service.celery_app import celery_app
from flask_service.tasks import test_task, run_scraper_task_v2

# テストタスク
result1 = test_task.delay('Hello from Flask')
print(f'Test Task ID: {result1.id}')

# scraper タスク（Django ORM 共有）
result2 = run_scraper_task_v2.delay(part_type='case')
print(f'Scraper Task ID: {result2.id}')
"
```

---

## フェーズ 6: 段階的移行（1 つのタスクを選んで）

### [ ] 6-1. 移行対象タスク選定
- [x] `run_scraper_task` （メインタスク）
  - 対象: `case`, `cpu`, `gpu`, `motherboard`, `storage` 等の部品スクレイピング

### [ ] 6-2. 既存 Django タスクの詳細確認
```powershell
cd f:\Python\Myportfolio
.\.venv\Scripts\Activate.ps1
cat django/scraper/tasks.py | head -50  # タスク定義確認
```

### [ ] 6-3. Flask 側でタスクを複製
```python
# flask_service/tasks.py に既存タスク定義をコピー+修正

@celery_app.task(name='flask_service.run_scraper_task')
def run_scraper_task(part_type, source='dospara', **kwargs):
    """
    Flask 経由での scraper タスク実行
    
    Django ORM は同じ DB を参照するため既存ロジックそのまま
    """
    import django
    django.setup()
    
    from scraper.dospara_scraper import DosparaScraper
    from scraper.models import PCPart
    
    scraper = DosparaScraper(part_type=part_type, source=source)
    items = scraper.fetch_items()
    
    # 既存の upsert ロジック
    for item in items:
        PCPart.objects.update_or_create(
            source_id=item['id'],
            parts_type=part_type,
            defaults={...}  # 既存フィールドマッピング
        )
    
    return f"Flask scraped {len(items)} items for {part_type}"
```

### [ ] 6-4. Django 側のタスク呼び出しを Flask に切り替え
```python
# django/scraper/views.py など、タスク呼び出し箇所を修正
# from scraper.tasks import run_scraper_task
# → from flask_service.tasks import run_scraper_task

# または Celery Task ルーター使用（推奨）
```

---

## フェーズ 7: Celery ワーカーをルーティング設定で管理（推奨）

### [ ] 7-1. `celery.py` にルーター設定を追加
```python
# django/myportfolio_django/celery.py

app.conf.task_routes = {
    'flask_service.run_scraper_task': {'queue': 'flask'},
    'flask_service.test_task': {'queue': 'flask'},
    'scraper.tasks.run_scraper_task': {'queue': 'django'},
}
```

### [ ] 7-2. キュー専用ワーカー起動
```powershell
# ターミナル 5：Django キュー用ワーカー
python manage.py celery -A myportfolio_django.celery worker -Q django -l info -P solo

# ターミナル 6：Flask キュー用ワーカー
python -m celery -A flask_service.celery_app worker -Q flask -l info -P solo
```

---

## フェーズ 8: 本番切り替え確認

### [ ] 8-1. Django 側タスク呼び出しが Flask に統一されているか確認
```powershell
cd f:\Python\Myportfolio
grep -r "run_scraper_task.delay" django/scraper/  # Django 側での直接呼び出し確認
```

### [ ] 8-2. ログで実行トレース
```pokershell
# Redis に入ったメッセージ確認
redis-cli
> LLEN celery
> LRANGE celery 0 -1
```

### [ ] 8-3. Django 側の古いタスク定義削除（最後に）
```powershell
# このステップは全テスト完了後に
# django/scraper/tasks.py から run_scraper_task を コメントアウト
```

---

## トラブルシューティング

| 症状 | 原因 | 対策 |
|------|------|------|
| `ConnectionRefusedError: Redis` | Redis 未起動 | `redis-server` を起動、または WSL/Docker で起動 |
| `ModuleNotFoundError: django` | Flask の celery_app で Django setup なし | `django.setup()` を tasks.py に追加 |
| `Task timeout` | ブローカー通信遅延 | `CELERY_TASK_SOFT_TIME_LIMIT` の値を増やす |
| `消えたタスク` | どちらのワーカーも受け取らない | `-Q` フラグと `task_routes` が一致しているか確認 |
| `Duplicate task execution` | 複数ワーカーが同じタスクを処理 | キュー分離設定を確認 |

---

## 最終検証チェックリスト

- [ ] Redis on 127.0.0.1:6379
- [ ] Django Celery worker running
- [ ] Flask Celery worker running
- [ ] テストタスク実行で両方のログに出力される
- [ ] Django ORM で DB 更新が確認できる
- [ ] エラーログなし（redis-log, celery-info）
- [ ] 30分以上連続実行での安定性確認
- [ ] Django migrationすべて [X] 状態

---

## 次のステップ（オプション）

1. **Celery Beat スケジューラー**: 定期スクレイピング（cron ジョブ）の移行
2. **Docker 化**: 本番環境での Celery + Redis のコンテナ化
3. **Prometheus/Grafana**: Celery タスクの監視ダッシュボード
4. **Django→FastAPI**: 将来的な API フレームワーク移行への準備

---

**作成日**: 2026-03-22  
**対象環境**: Windows 11, PowerShell 5.1  
**推定実行時間**: 30-60 分（Redis インストール除く）
