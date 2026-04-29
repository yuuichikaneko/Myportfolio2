# インデックス設計（3NF対応）

版数: v1.0  
最終更新日: 2026-03-21

## 1. 目的
構成生成APIおよび一覧APIで頻出する検索・並び替えの性能を改善するため、PostgreSQL向けインデックスを設計する。

## 2. 設計前提
- DB: PostgreSQL
- ORM: Django
- 主なアクセス傾向:
  - `PCPart` を `part_type` で絞り、`price` で並べる
  - `Configuration` を `is_deleted=False` で絞る
  - `ScraperStatus` を `updated_at DESC` で最新1件取得
  - `PCPart.name` の部分一致検索（DRF SearchFilter）

## 3. 既存インデックス（モデル定義由来）

### 3.1 PCPart
- 単一列INDEX: `maker`, `model_code`, `shop_code`, `socket`, `memory_type`, `chipset`, `form_factor`, `capacity_gb`, `interface`, `is_active`
- 複合UNIQUE: `(part_type, name)`
- FK INDEX: `manufacturer_id`

### 3.2 詳細テーブル
- `part_id` は OneToOne のため UNIQUE INDEX あり
- `ForeignKey` 列（`*_ref_id`）は自動INDEXあり
- `db_index=True` 指定列は単一INDEXあり

### 3.3 Configuration
- 単一列INDEX: `is_deleted`
- FK列は自動INDEXあり

### 3.4 ScraperStatus
- 明示INDEXなし

## 4. 追加インデックス設計（推奨）

### 4.1 最優先（P1）

#### IDX-P1-01: PCPart のカテゴリ別価格検索最適化
- 対象テーブル: `scraper_pcpart`
- 定義: `(part_type, price, name)`
- 効果:
  - `filter(part_type=...).order_by('price')`
  - `filter(part_type=...).order_by('price', 'name')`
  - `price__lt/price__gt + order_by('-price')`

#### IDX-P1-02: Configuration の有効データ取得最適化
- 対象テーブル: `scraper_configuration`
- 定義: `(is_deleted, created_at DESC)`
- 効果:
  - `filter(is_deleted=False)`
  - 最新順の一覧取得

#### IDX-P1-03: ScraperStatus 最新1件取得最適化
- 対象テーブル: `scraper_scraperstatus`
- 定義: `(updated_at DESC)`
- 効果:
  - `order_by('-updated_at').first()`

### 4.2 次点（P2）

#### IDX-P2-01: Configuration の用途別一覧最適化
- 対象テーブル: `scraper_configuration`
- 定義: `(usage, is_deleted, created_at DESC)`
- 効果:
  - `filter(usage=..., is_deleted=False)`

#### IDX-P2-02: Storage 専用の容量・価格検索最適化（部分インデックス）
- 対象テーブル: `scraper_pcpart`
- 定義: `(capacity_gb, price) WHERE part_type='storage'`
- 効果:
  - ストレージ在庫集計、容量帯フィルタ、価格順取得

### 4.3 追加検討（P3）

#### IDX-P3-01: name の部分一致検索最適化（pg_trgm）
- 対象テーブル: `scraper_pcpart`
- 前提: `pg_trgm` extension 有効化
- 定義: `GIN (name gin_trgm_ops)`
- 効果:
  - `icontains` / 部分一致検索高速化

#### IDX-P3-02: shop_code の条件付き一意性
- 対象テーブル: `scraper_pcpart`
- 定義: `UNIQUE (shop_code) WHERE shop_code IS NOT NULL AND shop_code <> ''`
- 効果:
  - データ重複の防止（運用品質向上）

## 5. 推奨DDL（PostgreSQL）

```sql
-- P1
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pcpart_type_price_name
ON scraper_pcpart (part_type, price, name);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_configuration_is_deleted_created_at
ON scraper_configuration (is_deleted, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scraperstatus_updated_at_desc
ON scraper_scraperstatus (updated_at DESC);

-- P2
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_configuration_usage_is_deleted_created_at
ON scraper_configuration (usage, is_deleted, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pcpart_storage_capacity_price
ON scraper_pcpart (capacity_gb, price)
WHERE part_type = 'storage';

-- P3
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pcpart_name_trgm
ON scraper_pcpart USING GIN (name gin_trgm_ops);

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_pcpart_shop_code_not_blank
ON scraper_pcpart (shop_code)
WHERE shop_code IS NOT NULL AND shop_code <> '';
```

## 6. Django実装時の注意
- `CONCURRENTLY` を使う場合、Django migration は `atomic = False` で作成する。
- 本番適用順序は P1 -> P2 -> P3。
- 適用後は `EXPLAIN ANALYZE` で実クエリ計画を確認する。

## 7. 効果測定指標
- `generate` API のP95応答時間
- `StorageInventoryAPIView` 応答時間
- `Configuration` 一覧APIの平均応答時間
- DB指標: seq scan 比率、index scan 比率、slow query件数
