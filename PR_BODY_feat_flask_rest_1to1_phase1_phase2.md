## Summary
- Django API の主要エンドポイントを Flask REST へ 1:1 で移植
- Django ロジック（serializer / configuration生成 / 集計）を Flask 側から再利用するブリッジを追加
- Frontend の既定 API 向き先を `http://127.0.0.1:8002/api` に変更
- 一括起動スクリプトで Flask API（8002）を同時起動するよう更新

## Background
- Phase 1: Django API -> Flask REST 1:1 移植
- Phase 2: テスト + 動作確認

## Changes
### Backend (Flask Bridge)
- `flask_service/django_bridge.py`
  - Django 設定と `django.setup()` を Flask 実行時に初期化
- `flask_service/app.py`
  - 既存 Django API 互換ルートを実装
  - 代表ルート:
    - `GET/POST /api/parts/`
    - `GET/PUT/PATCH/DELETE /api/parts/<id>/`
    - `GET/POST /api/configurations/`
    - `GET/PUT/PATCH/DELETE /api/configurations/<id>/`
    - `POST /api/configurations/generate/`
    - `GET /api/scraper-status/summary/`
    - `GET /api/market-price-range/`
    - `GET /api/part-price-ranges/`
    - `GET /api/storage-inventory/`
  - 互換ルート:
    - `POST /api/generate-config/`
    - `GET /api/scraper/status`
    - `POST /generate-config`
    - `GET /scraper/status`
- `flask_service/run_flask.py`
  - Flask 起動エントリポイント（127.0.0.1:8002）

### Startup Scripts
- `start_django_frontend.ps1`
  - Flask API 起動ステップ追加
  - Frontend 起動時に `VITE_API_URL=http://127.0.0.1:8002/api` を注入
- `start_django_frontend.bat`
  - Flask API 起動ステップ追加
  - Frontend 起動時に同様の `VITE_API_URL` を設定

### Frontend
- `frontend/src/api.ts`
  - API 既定URLを `http://127.0.0.1:8002/api` へ変更
  - 接続エラーメッセージを Flask ブリッジ起動案内に更新
- `frontend/src/api.test.ts`
  - API ベースURL変更に合わせて期待値を更新

### Dependencies
- `django/django_requirements.txt`
  - `Flask==3.1.2`
  - `Flask-Cors==6.0.1`

## Verification
### Automated
- Frontend unit tests: `10 passed`

### Manual
- Flask route map のロード確認
- HTTP疎通確認:
  - `GET http://127.0.0.1:8002/health` -> 200
  - `GET http://127.0.0.1:8002/api/scraper-status/summary/` -> 200
  - Frontend dev server -> 200
- ブラウザ表示確認: `http://127.0.0.1:5174`

## Notes / Risks
- Django admin / DRF browsable API (`/admin`, `/api-auth`) は Flask 側には移植していません
- 現段階は「1:1 API 互換の移行検証」目的のため、認証方式追加は次フェーズ

## Rollback
- Frontend の `VITE_API_URL` を Django (`http://127.0.0.1:8001/api`) に戻す
- 一括起動スクリプトの Flask 起動ステップを削除
- Flask 関連追加ファイルを除去
