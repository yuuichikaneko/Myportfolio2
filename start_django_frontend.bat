@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

for /f %%P in ('powershell -NoProfile -Command "$start=5173; $end=5200; for($p=$start; $p -le $end; $p++){ $l=$null; try { $l=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$p); $l.Start(); $l.Stop(); Write-Output $p; break } catch { if($l){$l.Stop()} } }"') do set FRONTEND_PORT=%%P

if "%FRONTEND_PORT%"=="" (
	echo Failed to find free frontend port in range 5173-5200.
	pause
	exit /b 1
)

echo ============================================================
echo Django + Frontend Startup
echo ============================================================
echo.

set REDIS_RUNNING=
for /f %%R in ('powershell -NoProfile -Command "$c=$null; try { $c=[System.Net.Sockets.TcpClient]::new('127.0.0.1',6379); if($c.Connected){'1'} } catch {} finally { if($c){$c.Dispose()} }"') do set REDIS_RUNNING=%%R
set PYTHON_EXE=%~dp0\.venv\Scripts\python.exe

if not "%REDIS_RUNNING%"=="1" (
	where redis-server >nul 2>nul
	if %errorlevel%==0 (
		echo [0] Redis not detected on 127.0.0.1:6379. Starting redis-server...
		start "Redis" cmd /k "redis-server"
		timeout /t 2 /nobreak >nul
	) else (
		echo [WARN] Redis is not running on 127.0.0.1:6379. Auto scraper may not work until Redis starts.
	)
)

set REDIS_READY=
for /f %%R in ('powershell -NoProfile -Command "$c=$null; try { $c=[System.Net.Sockets.TcpClient]::new('127.0.0.1',6379); if($c.Connected){'1'} } catch {} finally { if($c){$c.Dispose()} }"') do set REDIS_READY=%%R
if not "%REDIS_READY%"=="1" (
	echo [WARN] Redis is still unavailable on 127.0.0.1:6379. Celery Worker/Beat startup will be skipped.
)

echo [0.5] Applying Django migrations...
if exist "%PYTHON_EXE%" (
	cd django
	%PYTHON_EXE% manage.py migrate
	set MIGRATE_EXIT=%errorlevel%
	cd ..
) else (
	cd django
	py manage.py migrate
	set MIGRATE_EXIT=%errorlevel%
	cd ..
)

if not "%MIGRATE_EXIT%"=="0" (
	echo [WARN] Django migrations failed (exit code: %MIGRATE_EXIT%). Continuing startup, but app behavior may be unstable.
)

echo [1] Starting Django on port 8001...
if exist "%PYTHON_EXE%" (
	start "Django - Port 8001" cmd /k "cd django && %PYTHON_EXE% manage.py runserver 8001"
) else (
	start "Django - Port 8001" cmd /k "cd django && py manage.py runserver 8001"
)

timeout /t 2 /nobreak >nul

echo [1.5] Starting Flask API bridge on port 8002...
if exist "%PYTHON_EXE%" (
	start "Flask API - Port 8002" cmd /k "cd %~dp0 && %PYTHON_EXE% -m flask_service.run_flask"
) else (
	start "Flask API - Port 8002" cmd /k "cd %~dp0 && py -m flask_service.run_flask"
)

timeout /t 1 /nobreak >nul

echo [2] Starting Frontend on port %FRONTEND_PORT%...
start "Frontend - Port %FRONTEND_PORT%" cmd /k "cd frontend && set VITE_API_URL=http://127.0.0.1:8002/api && npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT%"

if "%REDIS_READY%"=="1" (
	timeout /t 1 /nobreak >nul

	echo [3] Starting Celery Worker (Auto Scraper)...
	if exist "%PYTHON_EXE%" (
		start "Celery Worker" cmd /k "cd django && %PYTHON_EXE% -m celery -A myportfolio_django worker -l info -P solo"
	) else (
		start "Celery Worker" cmd /k "cd django && py -m celery -A myportfolio_django worker -l info -P solo"
	)

	timeout /t 1 /nobreak >nul

	echo [4] Starting Celery Beat (Auto Scraper Scheduler)...
	if exist "%PYTHON_EXE%" (
		start "Celery Beat" cmd /k "cd django && %PYTHON_EXE% -m celery -A myportfolio_django beat -l info"
	) else (
		start "Celery Beat" cmd /k "cd django && py -m celery -A myportfolio_django beat -l info"
	)
) else (
	echo [3] Skipped Celery Worker startup (Redis unavailable).
	echo [4] Skipped Celery Beat startup (Redis unavailable).
)

echo.
echo ============================================================
echo Services Started
echo ============================================================
echo Django:   http://127.0.0.1:8001
echo Flask:    http://127.0.0.1:8002
echo Frontend: http://127.0.0.1:%FRONTEND_PORT%
echo Worker:   Celery Worker (auto scraper)
echo Beat:     Celery Beat (scheduler)
echo.
pause
