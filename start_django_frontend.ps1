Set-Location "$PSScriptRoot"

function Test-TcpPort {
	param(
		[string]$TargetHost = "127.0.0.1",
		[int]$Port
	)

	$client = $null
	try {
		$client = [System.Net.Sockets.TcpClient]::new()
		$client.Connect($TargetHost, $Port)
		return $true
	}
	catch {
		return $false
	}
	finally {
		if ($client) {
			$client.Dispose()
		}
	}
}

function Get-FreePort {
	param(
		[int]$StartPort = 5173,
		[int]$EndPort = 5200
	)

	for ($port = $StartPort; $port -le $EndPort; $port++) {
		$listener = $null
		try {
			$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
			$listener.Start()
			$listener.Stop()
			return $port
		}
		catch {
			if ($listener) {
				$listener.Stop()
			}
		}
	}

	throw "No free port found in range $StartPort-$EndPort"
}

Write-Output "============================================================"
Write-Output "Django + Frontend Startup"
Write-Output "============================================================"
Write-Output ""

$python = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) {
	$python = "py"
}

if (-not (Test-TcpPort -Port 6379)) {
	$redisCommand = Get-Command redis-server -ErrorAction SilentlyContinue
	if ($redisCommand) {
		Write-Output "[0] Redis not detected on 127.0.0.1:6379. Starting redis-server..."
		Start-Process powershell -ArgumentList '-NoExit', '-Command', "redis-server"
		Start-Sleep -Seconds 2
	}
	else {
		Write-Warning "Redis is not running on 127.0.0.1:6379. Auto scraper may not work until Redis starts."
	}
}

$redisReady = Test-TcpPort -Port 6379
if (-not $redisReady) {
	Write-Warning "Redis is still unavailable on 127.0.0.1:6379. Celery Worker/Beat startup will be skipped."
}

Write-Output "[0.5] Applying Django migrations..."
Push-Location "$PSScriptRoot/django"
& $python manage.py migrate
$migrateExitCode = $LASTEXITCODE
Pop-Location
if ($migrateExitCode -ne 0) {
	Write-Warning "Django migrations failed (exit code: $migrateExitCode). Continuing startup, but app behavior may be unstable."
}

Write-Output "[1] Starting Django on port 8001..."
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot/django'; & '$python' manage.py runserver 8001"

Start-Sleep -Seconds 2

Write-Output "[1.5] Starting Flask API bridge on port 8002..."
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot'; & '$python' -m flask_service.run_flask"

Start-Sleep -Seconds 1

$frontendPort = Get-FreePort
Write-Output "[2] Starting Frontend on port $frontendPort..."
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot/frontend'; $env:VITE_API_URL='http://127.0.0.1:8002/api'; npm run dev -- --host 127.0.0.1 --port $frontendPort"

Start-Sleep -Seconds 1

if ($redisReady) {
	Write-Output "[3] Starting Celery Worker (Auto Scraper)..."
	Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot/django'; & '$python' -m celery -A myportfolio_django worker -l info -P solo"

	Start-Sleep -Seconds 1

	Write-Output "[4] Starting Celery Beat (Auto Scraper Scheduler)..."
	Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$PSScriptRoot/django'; & '$python' -m celery -A myportfolio_django beat -l info"
}
else {
	Write-Output "[3] Skipped Celery Worker startup (Redis unavailable)."
	Write-Output "[4] Skipped Celery Beat startup (Redis unavailable)."
}

Write-Output ""
Write-Output "============================================================"
Write-Output "Services Started"
Write-Output "============================================================"
Write-Output "Django:   http://127.0.0.1:8001"
Write-Output "Flask:    http://127.0.0.1:8002"
Write-Output "Frontend: http://127.0.0.1:$frontendPort"
Write-Output "Worker:   Celery Worker (auto scraper)"
Write-Output "Beat:     Celery Beat (scheduler)"
