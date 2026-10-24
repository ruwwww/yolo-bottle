param(
    [int]$Epochs = 100,
    [int]$Batch = 16,
    [int]$Imgsz = 640,
    [string]$Model = "yolo11s.pt",
    [string]$Dataset = "dataset",
    [string]$Names = "",
    [string]$Out = "weights\multiclass_best.pt",
    [string]$HostName = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
& .venv\Scripts\python.exe src\split_dataset.py --keep-empty
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$cli = @(
    "src\train_remote.py", "--dataset", $Dataset, "--epochs", $Epochs,
    "--batch", $Batch, "--imgsz", $Imgsz, "--model", $Model, "--out", $Out
)
if ($Names) { $cli += @("--names", $Names) }
if ($HostName) { $cli += @("--host", $HostName) }
& .venv\Scripts\python.exe @cli
