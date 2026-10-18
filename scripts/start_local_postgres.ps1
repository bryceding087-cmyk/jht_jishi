[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'postgres_common.ps1')
Start-ProjectPostgres
