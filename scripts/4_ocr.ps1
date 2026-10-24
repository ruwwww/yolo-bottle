param(
    [string]$Source = "0",
    [string]$Roi = "",
    [string]$Image = "",
    [string]$Out = "ocr_captures",
    [switch]$NoWindow
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$args = @("src\ocr_payment.py", "--source", $Source, "--out", $Out)
if ($Roi) { $args += @("--roi", $Roi) }
if ($Image) { $args += @("--image", $Image) }
if ($NoWindow) { $args += "--no-window" }
& .venv\Scripts\python.exe @args
