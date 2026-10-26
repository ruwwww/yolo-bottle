param(
    [switch]$Info,
    [switch]$Test,
    [int]$Create = 0,
    [string]$Items = "Minuman Kasir Kulkas",
    [string]$Status = "",
    [string]$Poll = "",
    [string]$Cancel = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$cli = @("src\midtrans_pay.py")

if ($Test) {
    $cli += @("--test")
} elseif ($Create -gt 0) {
    $cli += @("--create", $Create, "--items", $Items)
} elseif ($Status) {
    $cli += @("--status", $Status)
} elseif ($Poll) {
    $cli += @("--poll", $Poll)
} elseif ($Cancel) {
    $cli += @("--cancel", $Cancel)
} else {
    $cli += @("--info")
}

& .venv\Scripts\python.exe @cli
exit $LASTEXITCODE
