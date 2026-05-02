Set-Location "$PSScriptRoot"

Write-Output "============================================================"
Write-Output "Django + Frontend Startup"
Write-Output "============================================================"
Write-Output ""

$python = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) {
	$python = "py"
}

Write-Output "[1] Starting Django on port 8001..."
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot/django'; & '$python' manage.py runserver 8001"

Start-Sleep -Seconds 2

$frontendPort = 5173
Write-Output "[2] Starting Frontend on port $frontendPort..."
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot/frontend'; npm run dev -- --host 127.0.0.1 --port $frontendPort --strictPort"

Write-Output ""
Write-Output "============================================================"
Write-Output "Services Started"
Write-Output "============================================================"
Write-Output "Django:   http://127.0.0.1:8001"
Write-Output "Frontend: http://127.0.0.1:$frontendPort"
