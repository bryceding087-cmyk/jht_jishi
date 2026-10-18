[CmdletBinding()]
param([string] $OutputPath = '')

. (Join-Path $PSScriptRoot 'postgres_common.ps1')
Assert-PostgresBinaries
$config = Read-PostgresConfig
if (-not $OutputPath) {
    $OutputPath = Join-Path $script:ProjectRoot ('data/backups/postgresql-' + (Get-Date -Format 'yyyyMMdd-HHmmss-ffff') + '.dump')
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $script:ProjectRoot $OutputPath
}
$backup = Assert-ProjectPath $OutputPath
if (Test-Path -LiteralPath $backup) { throw 'The backup target already exists; refusing to overwrite it.' }
$null = New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force
$previousPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $config.password
    $result = Invoke-PostgresCommand -Executable (Join-Path $script:PostgresRoot 'bin/pg_dump.exe') -Arguments @('-w', '-h', '127.0.0.1', '-p', $script:PostgresPort, '-U', $config.username, '-d', $config.database, '-Fc', '-f', $backup)
    if ($result.ExitCode -ne 0) {
        $safeOutput = $result.Output.Replace($config.password, '[redacted]').Replace($config.admin_password, '[redacted]')
        throw "PostgreSQL backup failed: $safeOutput"
    }
    $check = Invoke-PostgresCommand -Executable (Join-Path $script:PostgresRoot 'bin/pg_restore.exe') -Arguments @('--list', $backup)
    if ($check.ExitCode -ne 0) { throw 'The backup archive could not be read; retain the source database.' }
} finally { $env:PGPASSWORD = $previousPassword }
Write-Host ('PostgreSQL backup saved: ' + $backup)
