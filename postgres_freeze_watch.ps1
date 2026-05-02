param(
    [string]$EnvPath = 'django/.env',
    [int]$DurationSec = 300,
    [int]$IntervalSec = 2,
    [string]$OutDir = 'logs/postgres-freeze'
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$diag = Join-Path $root 'postgres_pg_activity.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python not found: $python"
}
if (-not (Test-Path -LiteralPath $diag)) {
    throw "Diagnostic script not found: $diag"
}

$resolvedOutDir = Join-Path $root $OutDir
New-Item -ItemType Directory -Force -Path $resolvedOutDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logPath = Join-Path $resolvedOutDir ("freeze_watch_{0}.log" -f $stamp)

function Append-Log {
    param([string]$Text)

    $Text | Out-File -LiteralPath $logPath -Append -Encoding utf8
}

function Write-Section {
    param([string]$Title)
    Append-Log -Text ""
    Append-Log -Text ("==== {0} ==== {1}" -f $Title, (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'))
}

function Invoke-Diag {
    param([string]$Action)

    Write-Section -Title ("action={0}" -f $Action)
    $output = & $python $diag --action $Action --env-path $EnvPath 2>&1
    if ($output) {
        foreach ($line in $output) {
            Append-Log -Text $line
        }
    }
    if ($LASTEXITCODE -ne 0) {
        Append-Log -Text ("diagnostic action '{0}' failed with exit code {1}" -f $Action, $LASTEXITCODE)
    }
}

$endAt = (Get-Date).AddSeconds($DurationSec)
Append-Log -Text ("freeze watch start={0}, duration={1}s, interval={2}s, env={3}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $DurationSec, $IntervalSec, $EnvPath)

while ((Get-Date) -lt $endAt) {
    Invoke-Diag -Action 'blockers'
    Invoke-Diag -Action 'locks'
    Invoke-Diag -Action 'snapshot'
    Start-Sleep -Seconds $IntervalSec
}

Append-Log -Text ("freeze watch end={0}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host "freeze watch completed: $logPath" -ForegroundColor Green
