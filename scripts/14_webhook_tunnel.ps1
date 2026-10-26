param(
    [int]$Port = 8088,
    [switch]$Start,
    [int]$Simulate = 0,
    [string]$Ref = "REF-SIMULASI-001"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if ($Simulate -gt 0) {
    Write-Host "Mengirim simulasi mutasi masuk Rp $Simulate ke port $Port..." -ForegroundColor Cyan
    & .venv\Scripts\python.exe src\webhook_server.py --port $Port --simulate $Simulate --ref $Ref
    exit $LASTEXITCODE
}

Write-Host "Menjalankan Webhook Server Mutasi Lokal (Port $Port)..." -ForegroundColor Green
& .venv\Scripts\python.exe src\webhook_server.py --port $Port
exit $LASTEXITCODE
