param(
    [int]$Epochs = 100,
    [int]$Batch = 8,
    [int]$Imgsz = 640,
    [string]$HostName = "",
    [string]$Model = "yolo11n.pt"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$args = @("src\train_remote.py", "--epochs", $Epochs, "--batch", $Batch, "--imgsz", $Imgsz, "--model", $Model)
if ($HostName) { $args += @("--host", $HostName) }
& .venv\Scripts\python.exe @args
