## 概要（Overview）

<!-- このPRの目的と背景を簡潔に説明してください -->
<!-- Describe the purpose and context of this PR in a concise manner -->

このPRは...

## 関連Issue

<!-- 関連する Issue がある場合は記載してください -->
<!-- Closes #123 -->
<!-- Related to #456 -->

- Closes: #

## 変更点（Changes）

### 📋 変更内容

- [ ] 機能追加
- [ ] バグ修正
- [ ] ドキュメント更新
- [ ] パフォーマンス最適化
- [ ] リファクタリング
- [ ] データベース スキーマ変更
- [ ] その他（スキーマ選択）

### 📝 詳細説明

<!-- 何が変更されたか、なぜそのような実装にしたのかを説明してください -->

| ファイル/層 | 説明 |
|-----------|------|
| ドキュメント | 新規追加/更新内容 |
| Django | Modelクラス、Migration、Serializer 等の変更 |
| API | エンドポイント、実装の変更 |
| Frontend | UI/UXの変更、機能追加 |

#### 主な変更ファイル

```
- docs/non_functional_requirements.md （新規）
- テーブル要件定義/データ型・制約定義_3NF.md （新規）
- テーブル要件定義/インデックス設計_3NF.md （新規）
- django/scraper/migrations/XXXX_add_performance_indexes.py （新規）
```

## 確認項目（Verification）

### 🔍 テスト / 検証

本PR をレビュー・マージする前に以下の確認をお願いします：

#### ドキュメント確認

- [ ] ER図が最新のモデル構造を反映しているか
- [ ] データ型・制約がすべてのテーブルに定義されているか
- [ ] インデックス設計の優先度（P1/P2/P3）が明確か
- [ ] NFR（非機能要件）が定量的で測定可能か

#### Migration 確認

```bash
# ローカルで Migration を確認
(.venv) $ python django/manage.py migrate --plan

# Migration を実行（dev環境）
(.venv) $ python django/manage.py migrate

# インデックスが正常に作成されたか確認
(.venv) $ python django/manage.py dbshell
postgres# SELECT * FROM pg_indexes WHERE tablename = 'scraper_pcpart';
postgres# \q
```

#### パフォーマンス確認

- [ ] 新規インデックスによるクエリ最適化が確認されたか
- [ ] `EXPLAIN ANALYZE` でクエリプランの改善を確認したか
- [ ] P1 インデックスのみで最小限の性能目標（P95 ≤3s）を達成できるか

#### 統合テスト

- [ ] Django の既存テストが全て通過するか
- [ ] API エンドポイントが正常に動作するか
- [ ] Scraper の実行に影響がないか

### ⚠️ リスク評価

- [ ] Database に大量のデータがある環境での Migration 実行時間を確認したか
- [ ] Rollback 手順が明確に文書化されているか
- [ ] 本番環境への展開順序は確定されているか

### 📊 デプロイメント手順

#### 開発環境

```bash
git checkout feat/nfr-index-migration
python django/manage.py migrate
# ドキュメント確認
```

#### ステージング環境

```bash
# Migration 実行（本番前の最終確認）
python django/manage.py migrate --plan
python django/manage.py migrate

# 性能テスト実行
```

#### 本番環境

```bash
# Migration の実行行分単位での確認推奨
# CREATE INDEX CONCURRENTLY により、テーブルロックなしでインデックス作成
python django/manage.py migrate
```

## 追加情報（Additional Info）

### 📚 参考資料

- [関連ドキュメント](https://github.com/yuuichikaneko/Myportfolio)
- Migration ファイル：`django/scraper/migrations/`

### 💡 その他のコメント

<!-- 必要に応じて追加情報を記載してください -->

---

## チェックリスト（Reviewer's Checklist）

マージ前に以下を確認してください：

- [ ] コミットメッセージが明確か
- [ ] コードレビュー コメントに対応しているか
- [ ] CI/CD パイプラインが成功しているか
- [ ] テストカバレッジが十分か
- [ ] ドキュメントが最新か
- [ ] Migration は idempotent か（複数回実行しても安全か）
