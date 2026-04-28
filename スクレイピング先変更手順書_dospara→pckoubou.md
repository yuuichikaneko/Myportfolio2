# スクレイピング先変更手順書
## ドスパラ → PC工房（pc-koubou.jp）

作成日: 2026-04-28  
対象リポジトリ: Myportfolio2

---

## 全体フロー

```
STEP1: PC工房のURL・HTML構造を調査する（手作業）
STEP2: pckoubou_scraper.py を新規作成する
STEP3: models.py の dospara_code フィールドを変更する
STEP4: migration を作成・適用する
STEP5: run_*.py スクリプトを書き換える
STEP6: tasks.py のインポートを差し替える
STEP7: DB をリセットして動作確認する
```

---

## STEP 1: 全カテゴリの category_id を確定する（完了）

全カテゴリの category_id を確認し、`django/scraper/pckoubou_scraper.py` の `CATEGORY_IDS` に設定しました。

### 確定した CATEGORY_IDS マッピング

| part_type | category_id | カテゴリ名 | URL |
|---|---|---|---|
| **cpu** | **1899** (Intel) | Intel CPU | `/category/040101.html` |
| **cpu** | **1900** (AMD) | AMD CPU | `/category/040102.html` |
| **motherboard** | **1902** (Intel) | Intel対応マザーボード | `/category/040401.html` |
| **motherboard** | **1903** (AMD) | AMD対応マザーボード | (products/list.php のみ) |
| **memory** | **1897** | デスクトップ用メモリ | `/category/040202.html` |
| **storage** | **2001** | SSD | (products/list.php のみ) |
| **gpu** | **1904** | グラフィックカード | `/category/040501.html` |
| **psu** | **1944** | 電源ユニット | `/category/040801.html` |
| **case** | **1921** | ミドルタワーケース | `/category/040602.html` |
| **cpu_cooler** | **1931** | CPUクーラー | `/category/040901.html` |

> CPU と Motherboard は Intel/AMD が別 category_id。スクレイパーは `List[int]` で自動的に両方取得します。

`pckoubou_scraper.py` の `CATEGORY_IDS` は更新・テスト済みです（全8カテゴリで取得確認済み）。

---

## STEP 2: `pckoubou_scraper.py` は完成しており、全カテゴリで動作確認済み

> `pckoubou_scraper.py` はすでに作成・テスト済みです。  
> **複数 category_id 対応**: CPU（Intel + AMD）など、複数の category_id を持つカテゴリは自動で全て取得します。

### 機能確認（テスト済み）

```
CPU:        Intel 13件 + AMD 7件   ✓
GPU:        5件
マザーボード: 5件
メモリ:      5件
ストレージ:   5件
電源:        5件
ケース:      5件
CPUクーラー: 5件
```

### ファイルの場所

`django/scraper/pckoubou_scraper.py` — **作成・テスト済み**

### 主要関数

- `scrape_pckoubou_category(part_type, max_items=500)`: 1 part_type で全 category_id を取得
- `scrape_pckoubou_all(max_items_per_category=500)`: 全 part_type を一括取得

---

## STEP 3: `models.py` の `dospara_code` フィールドを変更する

`PCPart` モデルの `dospara_code` フィールドをPC工房用に改名する。

### 変更ファイル: `django/scraper/models.py`

```python
# 変更前
dospara_code = models.CharField(max_length=50, blank=True, null=True, db_index=True)

# 変更後
shop_code = models.CharField(max_length=50, blank=True, null=True, db_index=True)
```

`_sync_normalized_fields` 内の参照も変更する:

```python
# 変更前
self.dospara_code = specs.get('code') or self.dospara_code

# 変更後
self.shop_code = specs.get('code') or self.shop_code
```

> ※ フィールド名を変えたくない場合は `dospara_code` のまま残しても動作上の問題はない。  
> その場合はSTEP4のmigrationが不要になるが、名前が紛らわしいので改名を推奨する。

---

## STEP 4: マイグレーションを作成・適用する

```powershell
# django ディレクトリに移動
cd F:\Python\Myportfolio2

# マイグレーションファイル作成
.venv\Scripts\python.exe django\manage.py makemigrations scraper --name "rename_dospara_code_to_shop_code"

# 適用
.venv\Scripts\python.exe django\manage.py migrate
```

> **DBを空にする場合**（DBはリセットしても問題ないとのことなので推奨）:
> ```powershell
> # SQLite の場合 — db.sqlite3 を削除して再作成
> Remove-Item django\db.sqlite3
> .venv\Scripts\python.exe django\manage.py migrate
> .venv\Scripts\python.exe django\create_superuser.py
> ```

---

## STEP 5: `run_*.py` スクリプトを書き換える

`django/run_sbr*.py` / `run_cpu*.py` / `run_gpu*.py` 等、各スクリプトを以下のパターンで書き換える。

### 変更前（例: `run_sbr1017_scrape.py`）

```python
import scraper.dospara_scraper as ds

URL = "https://www.dospara.co.jp/SBR1017?srule=01&includeNotInventory=false"
config = ds.get_dospara_scraper_config()
session = requests.Session()
resp = session.get(URL, headers=config["headers"], timeout=30)
codes = ds._collect_ic_codes_from_category_pages(...)
products_map = ds._fetch_products_by_codes(...)
parts = ds._build_parts_from_products_map(...)
```

### 変更後（統一パターン）

```python
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
django.setup()

from scraper.pckoubou_scraper import scrape_pckoubou_category
from scraper.models import PCPart

parts = scrape_pckoubou_category("cpu", max_items=500)  # part_type を変更

created = updated = skipped = 0
for p in parts:
    if not p.get("part_type"):
        skipped += 1
        continue
    _, is_created = PCPart.objects.update_or_create(
        url=p["url"],
        defaults={
            "name":         p["name"],
            "price":        p["price"],
            "part_type":    p["part_type"],
            "specs":        p.get("specs", {}),
            "stock_status": p.get("stock_status", "unknown"),
        },
    )
    if is_created:
        created += 1
    else:
        updated += 1

print(f"完了: created={created} updated={updated} skipped={skipped}")
```

### 書き換え対象ファイル一覧

| ファイル | 変更後の part_type |
|---|---|
| `run_cpu_8_14_scrape.py` | `"cpu"` |
| `run_cpu_16_24_scrape.py` | `"cpu"` |
| `run_cpu_amd_target_scrape.py` | `"cpu"` |
| `run_cpu_x3d_scrape.py` | `"cpu"` |
| `run_gpu_nvidia_rtx50_scrape.py` | `"gpu"` |
| `run_gpu_radeon_rx_scrape.py` | `"gpu"` |
| `run_motherboard_amd_scrape.py` | `"motherboard"` |
| `run_motherboard_intel_scrape.py` | `"motherboard"` |
| `run_sbr1017_scrape.py` | 調査後に part_type 決定 |
| `run_sbr1144_scrape.py` | 調査後に part_type 決定 |
| `run_sbr1416_scrape.py` | 調査後に part_type 決定 |
| `run_sbr1534_scrape.py` | 調査後に part_type 決定 |
| `run_workstation_vga_scrape.py` | `"gpu"` |
| `run_scraper.py` | 全カテゴリ（`scrape_pckoubou_all` を使用） |

---

## STEP 6: `tasks.py` のインポートを差し替える

### 変更ファイル: `django/scraper/tasks.py`

```python
# 変更前
from .dospara_scraper import (
    _infer_part_type,
    fetch_dospara_cpu_selection_material,
    fetch_dospara_gpu_performance_table,
    fetch_dospara_market_price_range,
    get_dospara_scraper_config,
    scrape_dospara_parts,
    scrape_dospara_category_parts,
)

# 変更後
from .pckoubou_scraper import (
    scrape_pckoubou_all,
    scrape_pckoubou_category,
)
```

> `fetch_dospara_cpu_selection_material`・`fetch_dospara_market_price_range` はドスパラ固有のAPI取得関数。  
> PC工房に同等データがない場合は、それらを呼んでいるタスクを一時的にコメントアウトする。  
> 該当タスクを `grep_search` で洗い出してから判断すること。

---

## STEP 7: DB リセット・動作確認

```powershell
# 1. DBリセット（SQLite）
Remove-Item django\db.sqlite3
.venv\Scripts\python.exe django\manage.py migrate
.venv\Scripts\python.exe django\create_superuser.py

# 2. 1カテゴリだけ試しにスクレイピングして確認
cd django
..\venv\Scripts\python.exe run_cpu_8_14_scrape.py

# 3. 件数確認
..\venv\Scripts\python.exe -c "
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'myportfolio_django.settings'
django.setup()
from scraper.models import PCPart
for pt, cnt in PCPart.objects.values_list('part_type').annotate():
    print(pt, cnt)
"
```

---

## 注意事項

### ドスパラとPC工房の構造の違い

| 項目 | ドスパラ | PC工房 |
|---|---|---|
| 商品取得方法 | 独自REST API（`getProducts`） | HTMLスクレイピング |
| 商品ID体系 | `ICxxxxxx`（IC番号） | URL内の数値ID |
| ページネーション | URLグリッド更新 | 通常のページリンク |
| スペック情報 | API JSONに含まれる | 商品詳細ページに分散 |

### `dospara_scraper.py` は削除しない
移行確認が取れるまで残しておく。完全移行後に削除する。

### `0016_postgres_partial_unique_dospara_code.py` について
マイグレーション `0016` が `dospara_code` カラムに部分ユニーク制約を作成している。  
STEP3でフィールド名を変更した場合は、新しいマイグレーションで制約も更新される。  
フィールド名を変更しない場合でも、PC工房のIDが入ることになるため実害はない。

---

## 作業チェックリスト

- [x] STEP1: 全8カテゴリ＋CPU(Intel/AMD)の category_id を確認
- [x] STEP2: `pckoubou_scraper.py` を作成した（Playwright版）
- [x] STEP2: Playwright + Chromium をインストール
- [x] STEP2: 複数 category_id に対応（CPU Intel+AMD 同時取得）
- [x] STEP2: 全カテゴリ動作確認済み
- [x] **STEP3**: `models.py` の `dospara_code` を改名 → `shop_code`（任意）
- [x] **STEP4**: `makemigrations` & `migrate` を実行
- [x] **STEP5**: 全 `run_*.py` を書き換え（PC工房版）
- [x] **STEP6**: `tasks.py` のインポート差し替え
- [x] **STEP7**: DBリセット + マイグレーション
- [x] **STEP8**: 全カテゴリ一括スクレイピング実行
- [x] **STEP9**: Django管理画面でデータ表示確認
