# PR本文テンプレート - feat/nfr-index-migration

以下のテンプレートを GitHub PR 作成時にコピー＆ペーストしてください。

---

## 概要（Overview）

このPRは、PC構成提案Webアプリの **データベース設計の完成と性能最適化** を目的としています。

### 背景

- 既存の 3NF スキーマ設計に基づいて、完全なドキュメンテーションを実施
- API のクエリパターンに応じたインデックス設計を推進
- 本番環境での性能目標（P95 ≤3s、P99 ≤5s）を達成するための基盤を構築

## 関連Issue

- 関連する Issue がある場合は記載してください

## 変更点（Changes）

### 📋 変更内容

- [x] ドキュメント更新（非機能要件、スキーマ仕様）
- [x] パフォーマンス最適化（インデックス設計）
- [x] データベース スキーマ変更（Migration ファイル作成）

### 📝 詳細説明

| ファイル/層 | 説明 |
|-----------|------|
| **ドキュメント** | 非機能要件（NFR）の定義ドキュメント作成 |
| **ドキュメント** | データ型・制約定義ドキュメント作成（全テーブル、100+ カラム） |
| **ドキュメント** | インデックス設計ドキュメント作成（P1/P2/P3 優先度付け） |
| **ドキュメント** | ER図簡略版作成（主要テーブルのみ） |
| **Django** | Migration ファイル作成（8 個のインデックス CREATE 操作） |

#### 主な変更ファイル

```
📁 新規作成（ドキュメント）
├── docs/non_functional_requirements.md
│   └── 性能、可用性、セキュリティ等の 6 カテゴリー
├── テーブル要件定義/ER図_3NF_簡略版.md
│   └── Mermaid ER図（簡略版）
├── テーブル要件定義/データ型・制約定義_3NF.md
│   └── 全テーブル、各カラムの型・制約・INDEX 定義
└── テーブル要件定義/インデックス設計_3NF.md
    └── P1/P2/P3 優先度別インデックス設計（8 個）

📁 新規作成（Django Migration）
└── django/scraper/migrations/0012_add_performance_indexes.py
    └── CREATE INDEX CONCURRENTLY 8 個の操作
```

### インデックス設計（P1/P2/P3）

**P1 - 優先度高（即座に実装）**
- `idx_pcpart_type_price_name`：API フィルター + ソート 最適化
- `idx_configuration_is_deleted_created_at`：ソフト削除パターン 最適化
- `idx_scraperstatus_updated_at_desc`：最新記録 取得 最適化

**P2 - 優先度中（第2フェーズ）**
- `idx_configuration_usage_is_deleted_created_at`：用途別フィルター
- `idx_pcpart_storage_capacity_price`：ストレージ検索 最適化（部分インデックス）

**P3 - 優先度低（先行投資）**
- `idx_pcpart_name_trgm`：全文検索対応（pg_trgm GIN）
- `uq_pcpart_dospara_code_not_blank`：部分一意制約

## 確認項目（Verification）

### 🔍 テスト / 検証チェックリスト

#### ドキュメント確認

- [ ] ER図が最新のモデル構造を反映しているか
- [ ] データ型・制約がすべてのテーブルに定義されているか
- [ ] インデックス設計の優先度（P1/P2/P3）が明確か
- [ ] NFR の 6 カテゴリー（性能、可用性、セキュリティ、運用、信頼性、データ品質）が網羅されているか
- [ ] 非機能要件の 6 つの受け入れ基準（NFR-01 ～ NFR-06）が定量的で測定可能か

#### Migration 確認

```bash
# ローカルで Migration 計画を確認
python django/manage.py migrate --plan

# Migration を実行（dev環境で動作確認）
python django/manage.py migrate

# インデックスが正常に作成されたか確認
python django/manage.py dbshell
# PostgreSQL プロンプトで以下を実行：
# SELECT * FROM pg_indexes WHERE schemaname = 'public' 
#   AND indexname LIKE 'idx_%' OR indexname LIKE 'uq_%';
```

#### パフォーマンス確認

- [ ] 新規インデックスによるクエリ最適化が確認されたか
- [ ] `EXPLAIN ANALYZE` でクエリプランの改善（Seq Scan → Index Scan）を確認したか
- [ ] P1 インデックスのみで API の性能目標（P95 ≤3s）を達成できるか
- [ ] インデックスサイズが許容範囲内か（想定: 各 10-50MB）

#### 統合テスト

- [ ] `python django/manage.py test` が全て通過するか
- [ ] Django の既存テストに失敗がないか
- [ ] API エンドポイント（generate-config）が正常に動作するか
- [ ] Scraper の実行に影響がないか（Scheduler が正常に動作するか）

#### Rollback 確認

- [ ] Rollback 時のコマンドが明確か：`python django/manage.py migrate scraper 0011`
- [ ] Rollback に要する時間の見積もりが取れているか（本番環境の想定）

### ⚠️ リスク評価

- [ ] Database に大量のデータがある環境での Migration 実行時間を確認したか（CONCURRENTLY で Lock フリー）
- [ ] 本番環境への展開順序（P1 → P2 → P3）は確定されているか
- [ ] Migration はべき等か（複数回実行しても安全か）

### 📊 デプロイメント手順

#### 開発環境

```bash
git checkout feat/nfr-index-migration
python django/manage.py migrate
# ドキュメント確認
```

#### ステージング環境

```bash
python django/manage.py migrate --plan
python django/manage.py migrate

# 性能テスト実行
# curl -X POST http://localhost:8001/api/generate-config/ -d {...}
```

#### 本番環境（推奨）

```bash
# P1 インデックスから段階的に実装
python django/manage.py migrate
# CREATE INDEX CONCURRENTLY のため、テーブルロックなし
```

## 追加情報（Additional Info）

### 📚 参考資料

- [ER図簡略版](./テーブル要件定義/ER図_3NF_簡略版.md)
- [データ型・制約定義](./テーブル要件定義/データ型・制約定義_3NF.md)
- [インデックス設計](./テーブル要件定義/インデックス設計_3NF.md)
- [非機能要件](./docs/non_functional_requirements.md)

### 💡 補足

- `atomic = False` を使用して、PostgreSQL の `CREATE INDEX CONCURRENTLY` を実行可能にしました
- インデックス作成中もテーブルへの読み書きが可能です（ダウンタイムなし）
- 本番環境では P1 インデックスから段階的に実装することを推奨します

---

## チェックリスト（Reviewer's Checklist）

マージ前に以下を確認してください：

- [ ] コミットメッセージが明確か：`Add NFR docs and index migration`
- [ ] コードレビュー コメントに対応しているか
- [ ] ドキュメントの日本語が正確か
- [ ] Migration ファイルに構文エラーがないか（`get_errors()` で検証済み）
- [ ] `atomic = False` が適切に設定されているか
- [ ] インデックス名が一意で、既存インデックスと重複していないか
- [ ] DDL の `IF NOT EXISTS` 句で idempotent 性が確保されているか
