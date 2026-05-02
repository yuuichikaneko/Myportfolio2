param(
    [int]$TimeoutSec = 300,
    [string]$EnvPath = 'django/.env',
    [switch]$SkipPlan,
    [switch]$AutoTerminateIdleBlockers,
    [int]$MinIdleTxSec = 30,
    [int]$RetryTimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$managePy = Join-Path $root 'django\manage.py'
$activityScript = Join-Path $root 'postgres_pg_activity.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python not found: $python"
}
if (-not (Test-Path -LiteralPath $managePy)) {
    throw "manage.py not found: $managePy"
}
if (-not (Test-Path -LiteralPath $activityScript)) {
    throw "Diagnostic script not found: $activityScript"
}

function Invoke-Diag {
    param([string]$Action)

    Write-Host "`n[diag] action=$Action" -ForegroundColor Cyan
    & $python $activityScript --action $Action --env-path $EnvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "diagnostic action '$Action' failed with exit code $LASTEXITCODE"
    }
}

function Invoke-MigrateOnce {
    param([int]$CurrentTimeoutSec)

    $stdoutPath = Join-Path $env:TEMP ("safe_migrate_stdout_{0}.log" -f [guid]::NewGuid().ToString('N'))
    $stderrPath = Join-Path $env:TEMP ("safe_migrate_stderr_{0}.log" -f [guid]::NewGuid().ToString('N'))

    $proc = Start-Process -FilePath $python `
        -ArgumentList @($managePy, 'migrate', '--noinput') `
        -WorkingDirectory $root `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $finished = $proc.WaitForExit($CurrentTimeoutSec * 1000)

    if (-not $finished) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        return @{
            Finished = $false
            ExitCode = 2
            StdoutPath = $stdoutPath
            StderrPath = $stderrPath
        }
    }

    $proc.Refresh()
    return @{
        Finished = $true
        ExitCode = [int]$proc.ExitCode
        StdoutPath = $stdoutPath
        StderrPath = $stderrPath
    }
}

function Show-MigrateOutput {
    param([hashtable]$Result)

    Write-Host '[4/4] migrate output' -ForegroundColor Yellow
    if (Test-Path -LiteralPath $Result.StdoutPath) {
        Get-Content -LiteralPath $Result.StdoutPath -Encoding UTF8
    }
    if (Test-Path -LiteralPath $Result.StderrPath) {
        $stderr = Get-Content -LiteralPath $Result.StderrPath -Encoding UTF8
        if ($stderr) {
            Write-Host "`nstderr:" -ForegroundColor DarkYellow
            $stderr
        }
    }
}

function Invoke-AutoTerminateIdleBlockers {
    Write-Host "`n[auto] idle blockers before terminate (min ${MinIdleTxSec}s)" -ForegroundColor DarkYellow
    & $python $activityScript --action 'idle-blockers' --min-idle-tx-sec $MinIdleTxSec --env-path $EnvPath

    Write-Host "[auto] terminate idle blockers" -ForegroundColor DarkYellow
    & $python $activityScript --action 'terminate-idle-blockers' --min-idle-tx-sec $MinIdleTxSec --env-path $EnvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "auto terminate action failed with exit code $LASTEXITCODE"
        return $false
    }

    Write-Host "[auto] blockers after terminate" -ForegroundColor DarkYellow
    Invoke-Diag -Action 'blockers'
    return $true
}

Write-Host '[1/4] PostgreSQL blockers snapshot (before migrate)' -ForegroundColor Yellow
Invoke-Diag -Action 'blockers'

if (-not $SkipPlan) {
    Write-Host '[2/4] migrate --plan' -ForegroundColor Yellow
    & $python $managePy migrate --plan
    if ($LASTEXITCODE -ne 0) {
        throw "migrate --plan failed with exit code $LASTEXITCODE"
    }
}

Write-Host '[3/4] migrate (timeout guarded)' -ForegroundColor Yellow
$result = Invoke-MigrateOnce -CurrentTimeoutSec $TimeoutSec

if (-not $result.Finished) {
    Write-Warning "migrate timed out after ${TimeoutSec}s. collecting diagnostics and stopping process."
    Invoke-Diag -Action 'snapshot'
    Invoke-Diag -Action 'blockers'
    Invoke-Diag -Action 'locks'

    if ($AutoTerminateIdleBlockers) {
        $terminated = Invoke-AutoTerminateIdleBlockers
        if ($terminated) {
            Write-Host "`n[auto] retry migrate once (timeout ${RetryTimeoutSec}s)" -ForegroundColor DarkYellow
            $retry = Invoke-MigrateOnce -CurrentTimeoutSec $RetryTimeoutSec
            Show-MigrateOutput -Result $retry
            if ($retry.Finished -and $retry.ExitCode -eq 0) {
                Write-Host 'migrate completed successfully after auto-terminate retry.' -ForegroundColor Green
                Invoke-Diag -Action 'snapshot'
                exit 0
            }
            Write-Warning "retry migrate failed. stdout log: $($retry.StdoutPath)"
            Write-Warning "retry migrate failed. stderr log: $($retry.StderrPath)"
        }
    }

    Write-Host "stdout log: $($result.StdoutPath)"
    Write-Host "stderr log: $($result.StderrPath)"
    exit $result.ExitCode
}

Show-MigrateOutput -Result $result
$exitCode = [int]$result.ExitCode

if ($exitCode -ne 0) {
    Write-Warning "migrate failed with exit code $exitCode."
    Invoke-Diag -Action 'blockers'
    Invoke-Diag -Action 'locks'
    exit $exitCode
}

Write-Host 'migrate completed successfully.' -ForegroundColor Green
Invoke-Diag -Action 'snapshot'