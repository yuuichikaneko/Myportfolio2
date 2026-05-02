# Myportfolio2

## レビュアー向けガイド（公開共有）

このリポジトリはURLによるポートフォリオレビュー用として公開しています。

- 対象範囲: Django + React (Vite) を使った構成ビルダー（データスクレイピング・運用診断機能含む）
- 対象者: リポジトリURLをお持ちの方

### 注目ポイント

- コア機能: PC構成自動生成とパーツ手動置換のUX
- データ品質: パソコン工房スクレイパーのupsertフローと整合性チェック
- 運用成熟度: タイムアウト付きマイグレーションとPostgreSQLロック診断

### セキュリティ・運用境界

- 運用ツールはローカル管理者専用ユーティリティです。
- これらのスクリプトをHTTPエンドポイント経由で公開しないでください。
- 通常の実行フローには含まれません。必要時に手動で実行してください。

## ドキュメント
- 要件定義: `docs/requirements.md`
- フロントエンド: `frontend/README.md`
- Django: `django/`
- Djangoパッケージ一覧: `django/DJANGO_INSTALLED_PACKAGES.txt`

## クイックスタート

### バックエンド（Django REST API）

```bash
cd f:\Python\Myportfolio2\django
f:\Python\Myportfolio2\.venv\Scripts\python.exe manage.py runserver 8001
```
`http://127.0.0.1:8001/api/` で起動

#### Django 初期セットアップ（初回のみ）

**1. Django依存パッケージをインストール・更新:**
```bash
cd f:\Python\Myportfolio2
f:\Python\Myportfolio2\.venv\Scripts\python.exe -m pip install -r django/django_requirements.txt
```

**2. PostgreSQL 環境変数を設定:**
`django/.env.postgresql.example` を参考に `django/.env` を作成してDB値を設定。
- Windowsでは `DB_CLIENT_ENCODING=UTF8` を維持してpsycopg2デコードエラーを防ぐ。
- `DJANGO_SECRET_KEY` の設定が必須。生成例:
  ```bash
  f:\Python\Myportfolio2\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
  ```

**3. データベースマイグレーション実行:**
```bash
cd f:\Python\Myportfolio2\django
f:\Python\Myportfolio2\.venv\Scripts\python.exe manage.py migrate
```

**4. DB接続確認:**
```bash
cd f:\Python\Myportfolio2\django
f:\Python\Myportfolio2\.venv\Scripts\python.exe manage.py showmigrations
```

> `DB_ENGINE` が `postgresql` に設定されていない場合、DjangoはSQLiteを使い続けます。

#### スクレイピング自動実行

`django/auto_run_scrapers.py` は `scraper.tasks.run_scraper_task()` を定期実行するランナーです。
現在のスクレイピング対象はパソコン工房（pc-koubou.jp）です。
補助データ（GPU性能表・市場価格帯・CPU選定素材）は既存インポート処理で更新されます。

**1回だけ実行:**
```bash
cd f:\Python\Myportfolio2\django
f:\Python\Myportfolio2\.venv\Scripts\python.exe auto_run_scrapers.py
```

**30分ごとに自動実行（失敗しても継続）:**
```bash
cd f:\Python\Myportfolio2\django
f:\Python\Myportfolio2\.venv\Scripts\python.exe auto_run_scrapers.py --interval-minutes 30 --continue-on-error
```

**3回だけ実行して終了:**
```bash
cd f:\Python\Myportfolio2\django
f:\Python\Myportfolio2\.venv\Scripts\python.exe auto_run_scrapers.py --interval-minutes 15 --max-runs 3 --continue-on-error
```

ログ出力先: `logs/auto_run_scrapers.log`

#### PostgreSQLマイグレーション・フリーズ対策と診断
運用ツールポリシー（ポートフォリオ範囲）:

- ローカル管理者専用。HTTPエンドポイント経由での公開禁止。
- 手動操作のみ。通常のアプリフローから自動実行しない。
- 共有環境では実行権限を指定オペレーターに限定。
- 本番環境ではデフォルト無効とし、インシデント対応時のみ有効化。

対象ツール:

- `postgres_pg_activity.py`
- `safe_postgres_migrate.ps1`
- `postgres_freeze_watch.ps1`

PostgreSQL使用時は `django/.env` に以下の変数を追加・調整してください:

```bash
DB_CONNECT_TIMEOUT=5
DB_STATEMENT_TIMEOUT_MS=15000
DB_LOCK_TIMEOUT_MS=5000
DB_IDLE_IN_TX_TIMEOUT_MS=10000
```

リポジトリルートからのクイック診断:

```bash
f:\Python\Myportfolio2\.venv\Scripts\python.exe postgres_pg_activity.py --action snapshot --env-path django/.env
f:\Python\Myportfolio2\.venv\Scripts\python.exe postgres_pg_activity.py --action blockers --env-path django/.env
f:\Python\Myportfolio2\.venv\Scripts\python.exe postgres_pg_activity.py --action locks --env-path django/.env
```

タイムアウト付きマイグレーション（VS Codeの長時間フリーズ防止に推奨）:

```powershell
./safe_postgres_migrate.ps1 -TimeoutSec 300 -EnvPath django/.env
```

ワンショット自動アンフリーズモード（タイムアウト → アイドルブロッカー検出 → 終了 → 1回リトライ）:

```powershell
./safe_postgres_migrate.ps1 -TimeoutSec 180 -AutoTerminateIdleBlockers -MinIdleTxSec 30 -RetryTimeoutSec 180 -EnvPath django/.env
```

またはVS Codeタスク `PostgreSQL Safe Migrate` から実行可能。

PowerShellヘルパー（psql使用）:

```powershell
./postgres_pg_activity_tools.ps1 -Action snapshot -EnvPath .\django\.env
./postgres_pg_activity_tools.ps1 -Action blockers -EnvPath .\django\.env
```

継続フリーズウォッチャー（ブロッカー・ロック・スナップショットを繰り返しログファイルに記録）:

```powershell
./postgres_freeze_watch.ps1 -EnvPath django/.env -DurationSec 300 -IntervalSec 2
```

ブロッカーPIDが特定できたら、まずcancelを使い、必要な場合のみterminateを使用:

```bash
f:\Python\Myportfolio2\.venv\Scripts\python.exe postgres_pg_activity.py --action cancel --target-pid <PID> --env-path django/.env
f:\Python\Myportfolio2\.venv\Scripts\python.exe postgres_pg_activity.py --action terminate --target-pid <PID> --env-path django/.env
```

Windows用ヘルパースクリプト:
- `start_django.bat`
- `start_django.ps1`
- `start_django_frontend.bat`
- `start_django_frontend.ps1`

`start_django_frontend.bat` / `start_django_frontend.ps1` は以下をすべて起動します:
- Djangoサーバー（8001）
- フロントエンド開発サーバー（空きポート自動選択）
- Celery Worker（パソコン工房自動スクレイパー）
- Celery Beat（スケジューラー）

`127.0.0.1:6379` でRedisが起動していない場合、`redis-server` が利用可能であれば自動起動を試みます。

### フロントエンド（React via Vite）

**Viteサーバーで起動：**
```bash
cd frontend
npm install
npm run dev
```
`http://127.0.0.1:5173` で起動（使用中の場合は次の空きポート）

#### CDN代替（Node.js不要）

`frontend/index-cdn.html` をブラウザで開くか、以下で配信:
```bash
python -m http.server -d frontend 8080
# http://localhost:8080/index-cdn.html を開く
```