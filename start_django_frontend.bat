@echo off
setlocal enabledelayedexpansion
cd /d %~dp0
set FRONTEND_PORT=5173

echo ============================================================
echo Django + Frontend Startup
echo ============================================================
echo.

echo [1] Starting Django on port 8001...
set PYTHON_EXE=%~dp0\.venv\Scripts\python.exe
if exist "%PYTHON_EXE%" (
	start "Django - Port 8001" cmd /k "cd django && %PYTHON_EXE% manage.py runserver 8001"
) else (
	start "Django - Port 8001" cmd /k "cd django && py manage.py runserver 8001"
)

timeout /t 2 /nobreak >nul

echo [2] Starting Frontend on port %FRONTEND_PORT%...
start "Frontend - Port %FRONTEND_PORT%" cmd /k "cd frontend && npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort"

echo.
echo ============================================================
echo Services Started
echo ============================================================
echo Django:   http://127.0.0.1:8001
echo Frontend: http://127.0.0.1:%FRONTEND_PORT%
echo.
pause
