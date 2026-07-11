$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $backendDir
Set-Location -LiteralPath $projectDir

# This script runs on the host machine, not inside the Docker network.
# It connects to PostgreSQL through the host port published by docker-compose.yml:
# 127.0.0.1:5430 -> postgres:5432.
function Get-EnvFileValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*$Key\s*=\s*(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $null
}

$envFile = Join-Path $projectDir ".env"
$dbPassword = Get-EnvFileValue -Path $envFile -Key "DATAMID_DB_PASSWORD"
if (-not $dbPassword) {
    throw "DATAMID_DB_PASSWORD not found in $envFile"
}

$env:DATAMID_DB_HOST = "127.0.0.1"
$env:DATAMID_DB_PORT = "5430"
$env:DATAMID_DB_NAME = "datamid"
$env:DATAMID_DB_USER = "datamid"
$env:DATAMID_DB_PASSWORD = $dbPassword

$envValue = [Environment]::GetEnvironmentVariable("DATAMID_RAMON_AUTH", "Process")
if (-not $envValue) {
    $envValue = [Environment]::GetEnvironmentVariable("DATAMID_RAMON_AUTH", "User")
}
if (-not $envValue) {
    $envValue = [Environment]::GetEnvironmentVariable("DATAMID_RAMON_AUTH", "Machine")
}
if (-not $envValue) {
    throw "DATAMID_RAMON_AUTH is not configured."
}

$env:DATAMID_RAMON_AUTH = $envValue

$pythonPath = "C:\Users\1202605003\AppData\Local\miniconda3\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
}

& $pythonPath -m backend.app.tasks.sync_runner --triggered-by "scheduler_2am"
