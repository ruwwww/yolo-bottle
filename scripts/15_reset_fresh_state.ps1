param(
    [switch]$NoArchive
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$cli = @("src\db.py", "--fresh")
if ($NoArchive) {
    $cli += "--no-archive"
}

Write-Host "Menjalankan reset sistem kasir ke Fresh State..." -ForegroundColor Cyan
& .venv\Scripts\python.exe @cli
exit $LASTEXITCODE
