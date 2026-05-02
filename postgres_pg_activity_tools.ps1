param(
    [ValidateSet('snapshot', 'blockers', 'cancel', 'terminate')]
    [string]$Action = 'snapshot',
    [int]$TargetPid = 0,
    [string]$EnvPath = '.\django\.env',
    [string]$PsqlPath = 'psql'
)

$ErrorActionPreference = 'Stop'

function Read-EnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Env file not found: $Path"
    }

    $result = @{}
    $lines = Get-Content -LiteralPath $Path -Encoding UTF8
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        $idx = $trimmed.IndexOf('=')
        if ($idx -lt 1) {
            continue
        }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim()
        $result[$key] = $value
    }
    return $result
}

function Invoke-Psql {
    param(
        [string]$Sql,
        [hashtable]$Env
    )

    $dbName = $Env['DB_NAME']
    $dbUser = $Env['DB_USER']
    $dbHost = $Env['DB_HOST']
    $dbPort = $Env['DB_PORT']
    $dbPass = $Env['DB_PASSWORD']

    if (-not $dbName -or -not $dbUser -or -not $dbHost -or -not $dbPort) {
        throw 'Required DB settings are missing in env file.'
    }

    $env:PGPASSWORD = $dbPass
    & $PsqlPath -X -v ON_ERROR_STOP=1 -P pager=off -h $dbHost -p $dbPort -U $dbUser -d $dbName -c $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "psql exited with code $LASTEXITCODE"
    }
}

$envMap = Read-EnvFile -Path $EnvPath

if ($envMap['DB_ENGINE'] -and $envMap['DB_ENGINE'].ToLower() -notin @('postgres', 'postgresql', 'django.db.backends.postgresql')) {
    throw "DB_ENGINE is not PostgreSQL. Current: $($envMap['DB_ENGINE'])"
}

switch ($Action) {
    'snapshot' {
        $sql = @"
SELECT
  now() AS observed_at,
  pid,
  usename,
  application_name,
  client_addr,
  state,
  wait_event_type,
  wait_event,
  now() - query_start AS query_age,
  now() - xact_start AS xact_age,
  pg_blocking_pids(pid) AS blocking_pids,
  LEFT(query, 240) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY query_start NULLS LAST;
"@
        Invoke-Psql -Sql $sql -Env $envMap
    }
    'blockers' {
        $sql = @"
WITH waiting AS (
  SELECT pid, unnest(pg_blocking_pids(pid)) AS blocker_pid
  FROM pg_stat_activity
  WHERE cardinality(pg_blocking_pids(pid)) > 0
)
SELECT
  w.pid AS waiting_pid,
  wa.usename AS waiting_user,
  now() - wa.query_start AS waiting_for,
  LEFT(wa.query, 180) AS waiting_query,
  w.blocker_pid,
  ba.usename AS blocker_user,
  ba.state AS blocker_state,
  now() - ba.xact_start AS blocker_xact_age,
  LEFT(ba.query, 180) AS blocker_query
FROM waiting w
JOIN pg_stat_activity wa ON wa.pid = w.pid
JOIN pg_stat_activity ba ON ba.pid = w.blocker_pid
ORDER BY waiting_for DESC;
"@
        Invoke-Psql -Sql $sql -Env $envMap
    }
    'cancel' {
        if ($TargetPid -le 0) {
            throw 'Use -TargetPid for cancel action.'
        }
        $sql = "SELECT pg_cancel_backend($TargetPid) AS canceled_pid_$TargetPid;"
        Invoke-Psql -Sql $sql -Env $envMap
    }
    'terminate' {
        if ($TargetPid -le 0) {
            throw 'Use -TargetPid for terminate action.'
        }
        $sql = "SELECT pg_terminate_backend($TargetPid) AS terminated_pid_$TargetPid;"
        Invoke-Psql -Sql $sql -Env $envMap
    }
}
