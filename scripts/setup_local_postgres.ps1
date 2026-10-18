[CmdletBinding()]
param(
    # Optional existing official EDB PostgreSQL 17.11 Windows x64 binary ZIP.
    [string] $ArchivePath = ''
)

. (Join-Path $PSScriptRoot 'postgres_common.ps1')

$runtimeParent = Assert-ProjectPath (Join-Path $script:ProjectRoot '.runtime')
$dataParent = Assert-ProjectPath (Join-Path $script:ProjectRoot 'data')
$downloadPath = Assert-ProjectPath (Join-Path $runtimeParent 'postgresql-17.11-windows-x64.zip')
$downloadUrl = 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260616'
# Pin the archive retrieved from the official EDB page for reproducible installs.
$archiveSha256 = '80379B2C04D51C30225532E0AE04509899141E9957ED096FE749D7FD9DF8F82F'
$envPath = Assert-ProjectPath (Join-Path $script:ProjectRoot '.env')

if ((Test-Path -LiteralPath $script:PostgresData) -and -not (Test-Path -LiteralPath $script:PostgresConfig)) {
    throw 'An existing PostgreSQL data directory has no project configuration. Refusing to adopt or overwrite it.'
}
if (-not (Test-Path -LiteralPath $script:PostgresData) -and (Test-PostgresPort)) {
    throw "Port $script:PostgresPort is already occupied. Refusing to create a conflicting instance."
}
$null = New-Item -ItemType Directory -Path $runtimeParent -Force
$null = New-Item -ItemType Directory -Path $dataParent -Force

if (-not (Test-Path -LiteralPath $script:PostgresRoot)) {
    if ($ArchivePath) {
        $archive = (Resolve-Path -LiteralPath $ArchivePath -ErrorAction Stop).Path
    } else {
        $archive = $downloadPath
        if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
            $partial = Assert-ProjectPath ($archive + '.download')
            Write-Host 'Downloading the official EDB PostgreSQL 17.11 Windows x64 portable archive...'
            $previousProgress = $ProgressPreference
            try {
                $ProgressPreference = 'SilentlyContinue'
                Invoke-WebRequest -Uri $downloadUrl -UseBasicParsing -OutFile $partial -TimeoutSec 1800
            } catch {
                throw 'Official PostgreSQL download failed. No database was initialized.'
            } finally { $ProgressPreference = $previousProgress }
            Move-Item -LiteralPath $partial -Destination $archive
        }
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $archiveSha256) {
        throw 'The PostgreSQL archive checksum differs from the pinned official 17.11 download. No database was initialized.'
    }
    $extractDirectory = Assert-ProjectPath (Join-Path $runtimeParent ('postgresql-extract-' + [guid]::NewGuid().ToString('N')))
    $null = New-Item -ItemType Directory -Path $extractDirectory
    Expand-Archive -LiteralPath $archive -DestinationPath $extractDirectory
    $extractedRoot = Assert-ProjectPath (Join-Path $extractDirectory 'pgsql')
    if (-not (Test-Path -LiteralPath (Join-Path $extractedRoot 'bin/postgres.exe') -PathType Leaf)) {
        throw 'The archive does not contain the expected official pgsql/bin/postgres.exe layout.'
    }
    $null = Assert-ProjectPath $script:PostgresRoot
    Move-Item -LiteralPath $extractedRoot -Destination $script:PostgresRoot
    # Remove only this named empty staging directory, never a data directory.
    if (@(Get-ChildItem -LiteralPath $extractDirectory -Force).Count -eq 0) {
        Remove-Item -LiteralPath $extractDirectory
    }
}
Assert-PostgresBinaries

function New-LocalPassword {
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $random.GetBytes($bytes)
        return -join ($bytes | ForEach-Object { $_.ToString('x2') })
    } finally { $random.Dispose() }
}

if (Test-Path -LiteralPath $script:PostgresConfig) {
    $config = Read-PostgresConfig
} else {
    $config = [pscustomobject]@{
        version = $script:PostgresVersion
        host = '127.0.0.1'
        port = $script:PostgresPort
        data_directory = $script:PostgresData
        database = 'jianhuitong'
        username = 'jianhuitong_app'
        password = New-LocalPassword
        admin_password = New-LocalPassword
    }
    Write-LocalUtf8 $script:PostgresConfig ($config | ConvertTo-Json)
}

if (-not (Test-Path -LiteralPath (Join-Path $script:PostgresData 'PG_VERSION') -PathType Leaf)) {
    $passwordFile = Assert-ProjectPath (Join-Path $dataParent '.postgres-init-password')
    try {
        Write-LocalUtf8 $passwordFile ($config.admin_password + "`n")
        $initResult = Invoke-PostgresCommand -Executable (Join-Path $script:PostgresRoot 'bin/initdb.exe') -Arguments @('-D', $script:PostgresData, '-U', 'postgres', '--encoding=UTF8', '--locale=C', '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', ('--pwfile=' + $passwordFile))
        if ($initResult.ExitCode -ne 0) {
            $safeOutput = $initResult.Output.Replace($config.admin_password, '[redacted]').Replace($config.password, '[redacted]')
            throw "PostgreSQL initialization failed: $safeOutput"
        }
    } finally {
        if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    }
}

# This project-only include fixes the server to the loopback interface and SCRAM.
$localServerConfig = Assert-ProjectPath (Join-Path $script:PostgresData 'jianhuitong.conf')
Write-LocalUtf8 $localServerConfig "listen_addresses = '127.0.0.1'`nport = $script:PostgresPort`npassword_encryption = 'scram-sha-256'`n"
$mainServerConfig = Join-Path $script:PostgresData 'postgresql.conf'
$mainContent = [System.IO.File]::ReadAllText($mainServerConfig)
if ($mainContent -notmatch "(?m)^\s*include\s*=\s*'jianhuitong\.conf'\s*$") {
    Write-LocalUtf8 $mainServerConfig ($mainContent + "`ninclude = 'jianhuitong.conf'`n")
}

Start-ProjectPostgres
$psql = Join-Path $script:PostgresRoot 'bin/psql.exe'
$previousPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $config.admin_password
    # Passwords travel over stdin and the child environment, never command arguments.
    $bootstrap = @'
SELECT 'CREATE ROLE jianhuitong_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD ''__APP_PASSWORD__'''
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jianhuitong_app')
\gexec
SELECT 'CREATE DATABASE jianhuitong OWNER jianhuitong_app'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'jianhuitong')
\gexec
REVOKE ALL ON DATABASE jianhuitong FROM PUBLIC;
'@.Replace('__APP_PASSWORD__', $config.password)
    $bootstrapResult = Invoke-PostgresCommand -Executable $psql -Arguments @('-X', '-w', '-h', '127.0.0.1', '-p', $script:PostgresPort, '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-q') -InputText $bootstrap
    if ($bootstrapResult.ExitCode -ne 0) {
        $safeOutput = $bootstrapResult.Output.Replace($config.admin_password, '[redacted]').Replace($config.password, '[redacted]')
        throw "PostgreSQL application database setup failed: $safeOutput"
    }
    $env:PGPASSWORD = $config.password
    $checkResult = Invoke-PostgresCommand -Executable $psql -Arguments @('-X', '-w', '-h', '127.0.0.1', '-p', $script:PostgresPort, '-U', $config.username, '-d', $config.database, '-v', 'ON_ERROR_STOP=1', '-t', '-A') -InputText 'SELECT current_database(), current_user;'
    if ($checkResult.ExitCode -ne 0 -or $checkResult.Output -ne 'jianhuitong|jianhuitong_app') {
        throw 'The application database login check failed; credentials were not printed.'
    }
} finally { $env:PGPASSWORD = $previousPassword }

$databaseUrl = 'postgresql+psycopg://jianhuitong_app:' + $config.password + '@127.0.0.1:' + $script:PostgresPort + '/jianhuitong'
$envContent = if (Test-Path -LiteralPath $envPath) { [System.IO.File]::ReadAllText($envPath) } else { '' }
if ($envContent -match '(?m)^\s*JIANHUITONG_DATABASE_URL\s*=') {
    Write-Host 'Existing JIANHUITONG_DATABASE_URL was preserved without modification.'
} else {
    $separator = if ($envContent -and -not $envContent.EndsWith("`n")) { "`n" } else { '' }
    [System.IO.File]::AppendAllText($envPath, ($separator + "JIANHUITONG_DATABASE_URL=$databaseUrl`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host 'The local database connection was appended to .env.'
}
Write-Host 'Local PostgreSQL is ready: database jianhuitong; application role jianhuitong_app.'
Write-Host 'Credentials remain in ignored local files: .env and data/postgresql.local.json.'
