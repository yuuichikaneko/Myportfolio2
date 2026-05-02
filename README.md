# Myportfolio

## Documents
- Requirements: `docs/requirements.md`
- Frontend: `frontend/README.md`
- Django: `django/`
- Django packages: `django/DJANGO_INSTALLED_PACKAGES.txt`
- FastAPI: moved to `F:\Python\Myportfolio_FastAPI\backend`

## Project Split
- FastAPI files: `F:\Python\Myportfolio_FastAPI\backend`
- Django files: `django/`
- FastAPI helper scripts: `F:\Python\Myportfolio_FastAPI\backend\scripts`

## Quick Start

### Backend (FastAPI)
```bash
cd F:\Python\Myportfolio_FastAPI\backend
# 初回のみ: 仮想環境を作成してパッケージをインストール
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 起動 (仮想環境を有効化してから)
.venv\Scripts\activate
python -m uvicorn app.main:app --reload
```
Runs on `http://localhost:8000`

> **Note:** このプロジェクト (`Myportfolio2`) の `.venv` とは別の仮想環境です。
> FastAPI の起動は必ず `F:\Python\Myportfolio_FastAPI\backend\.venv` を有効化して行ってください。
> ブラウザから `/favicon.ico` へのアクセスで `404 Not Found` が出ることがありますが、ファビコン未設定時の通常動作です。

### Django
```bash
cd django
python manage.py runserver 8001
```
Runs on `http://localhost:8001`

> **Note:** `http://127.0.0.1:8001/` のトップページは、開発中の React フロントエンド `http://127.0.0.1:5173` へリダイレクトされます。
> フロント画面を表示するには Vite 開発サーバーも起動してください。

Windows helper scripts:
- `start_django.bat`
- `start_django.ps1`
- `start_django_frontend.bat`
- `start_django_frontend.ps1`

### Scraper (Django scripts)
```bash
cd django

# 例: 単発のスクレイプを実行
python run_scraper.py

# 例: カテゴリ別スクレイプを実行
python run_storage_scrape.py
python run_motherboard_intel_scrape.py
python run_motherboard_amd_scrape.py
```

Repository root には PowerShell / BAT の補助スクリプトもあります:
- `scrape_br116.ps1`
- `scrape_case_br72.ps1`
- `scrape_cpu_cooler_br95.ps1`
- `scrape_memory_br12.ps1`
- `scrape_motherboard_br21.ps1`

> **Note:** スクレイプ実行前に `Myportfolio2` の `.venv` を有効化してください。
> 実行後に構成提案へ反映させる場合は、Django サーバー（`python manage.py runserver 8001`）を起動した状態で確認してください。

### Frontend (React via Vite)
```bash
cd frontend
npm install
npm run dev
```
Runs on `http://127.0.0.1:5173`

### Frontend (CDN Alternative - No Node.js required)
Open `frontend/index-cdn.html` in a browser or serve with:
```bash
python -m http.server -d frontend 8080
# Open http://localhost:8080/index-cdn.html
```