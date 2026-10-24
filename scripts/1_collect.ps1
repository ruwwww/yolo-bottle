param(
    [string]$Source = "0",
    [int]$Max = 50,
    [double]$Conf = 0.5,
    [switch]$NoWindow
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$args = @("src\autolabel.py", "--source", $Source, "--max", $Max, "--conf", $Conf)
if ($NoWindow) { $args += "--no-window" }
& .venv\Scripts\python.exe @args
& .venv\Scripts\python.exe src\split_dataset.py
