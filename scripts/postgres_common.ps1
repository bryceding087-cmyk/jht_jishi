Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:PostgresRoot = Join-Path $script:ProjectRoot '.runtime/postgresql'
$script:PostgresData = Join-Path $script:ProjectRoot 'data/postgresql'
$script:PostgresConfig = Join-Path $script:ProjectRoot 'data/postgresql.local.json'
$script:PostgresPort = 55432
$script:PostgresVersion = '17.11'

function Assert-ProjectPath([string] $Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $script:ProjectRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'PostgreSQL files must remain inside this project workspace.'
    }
    return $resolved
}

function Write-LocalUtf8([string] $Path, [string] $Content) {
    $resolved = Assert-ProjectPath $Path
    [System.IO.File]::WriteAllText($resolved, $Content, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-PostgresCommand {
    param([string] $Executable, [string[]] $Arguments, [string] $InputText)
    $previousErrorAction = $ErrorActionPreference
    try {
        # Capture native stderr before reporting errors so callers can redact secrets.
        $ErrorActionPreference = 'Continue'
        if ($PSBoundParameters.ContainsKey('InputText')) {
            $output = $InputText | & $Executable @Arguments 2>&1
        } else {
            $output = & $Executable @Arguments 2>&1
        }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output | Out-String).Trim() }
    } finally { $ErrorActionPreference = $previousErrorAction }
}

function Read-PostgresConfig {
    if (-not (Test-Path -LiteralPath $script:PostgresConfig -PathType Leaf)) {
        throw 'Local PostgreSQL configuration is missing. Run scripts/setup_local_postgres.ps1 first.'
    }
    $config = Get-Content -LiteralPath $script:PostgresConfig -Raw | ConvertFrom-Json
    if ($config.host -ne '127.0.0.1' -or [int]$config.port -ne $script:PostgresPort -or
        [System.IO.Path]::GetFullPath($config.data_directory) -ne $script:PostgresData -or
        $config.database -ne 'jianhuitong' -or $config.username -ne 'jianhuitong_app' -or
        $config.version -ne $script:PostgresVersion) {
        throw 'Existing PostgreSQL configuration does not match this local installation; no files were overwritten.'
    }
    return $config
}

function Assert-PostgresBinaries {
    $exe = Join-Path $script:PostgresRoot 'bin/postgres.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw 'PostgreSQL binaries are missing. Run scripts/setup_local_postgres.ps1 first.'
    }
    $versionOutput = (& $exe --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch ('\b' + [regex]::Escape($script:PostgresVersion) + '\b')) {
        throw 'The portable runtime is not the expected PostgreSQL 17.11 release.'
    }
}

function Test-PostgresPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $script:PostgresPort, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(1000)) { return $false }
        try { $client.EndConnect($pending); return $true } catch { return $false }
    } finally { $client.Dispose() }
}

function Start-ProjectPostgres {
    Assert-PostgresBinaries
    $null = Read-PostgresConfig
    if (-not (Test-Path -LiteralPath (Join-Path $script:PostgresData 'PG_VERSION') -PathType Leaf)) {
        throw 'The local PostgreSQL data directory is not initialized.'
    }
    $pgCtl = Join-Path $script:PostgresRoot 'bin/pg_ctl.exe'
    $null = & $pgCtl -D $script:PostgresData status 2>&1
    if ($LASTEXITCODE -eq 0) {
        $null = & (Join-Path $script:PostgresRoot 'bin/pg_isready.exe') -h 127.0.0.1 -p $script:PostgresPort -q
        if ($LASTEXITCODE -ne 0) { throw 'The recorded PostgreSQL process is not accepting local connections.' }
        Write-Host "PostgreSQL $script:PostgresVersion is already running on 127.0.0.1:$script:PostgresPort."
        return
    }
    if (Test-PostgresPort) {
        throw "Port $script:PostgresPort is already in use by another process; no process was stopped."
    }
    $serverLog = Assert-ProjectPath (Join-Path $script:ProjectRoot 'data/postgresql-server.log')
    $stdoutLog = Assert-ProjectPath (Join-Path $script:ProjectRoot 'data/postgresql-start.out.log')
    $stderrLog = Assert-ProjectPath (Join-Path $script:ProjectRoot 'data/postgresql-start.err.log')
    $launchArgs = @('-D', ('"{0}"' -f $script:PostgresData), '-l', ('"{0}"' -f $serverLog), '-w', '-t', '45', 'start')
    $process = Start-Process -FilePath $pgCtl -ArgumentList $launchArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    if (-not $process.WaitForExit(55000)) { throw 'PostgreSQL startup timed out; inspect data/postgresql-start.err.log.' }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw 'PostgreSQL startup failed; inspect data/postgresql-start.err.log and data/postgresql-server.log.' }
    Write-Host "PostgreSQL $script:PostgresVersion started on 127.0.0.1:$script:PostgresPort."
}
