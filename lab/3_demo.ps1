param(
    [string]$Weights = "weights\bottle_best.pt",
    [string]$Source = "0",
    [double]$Conf = 0.35,
    [string]$SaveDir = "",
    [switch]$Fullscreen
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$args = @("src\demo_yolo.py", "--weights", $Weights, "--source", $Source, "--conf", $Conf)
if ($SaveDir) { $args += @("--save-dir", $SaveDir) }
if ($Fullscreen) { $args += "--fullscreen" }
& .venv\Scripts\python.exe @args
