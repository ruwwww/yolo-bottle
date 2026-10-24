param(
    [string]$Source = "0",
    [string]$PayImage = "",
    [string]$Model = "weights\multiclass_best.pt",
    [string]$Menu = "menu_multiclass.csv",
    [double]$Conf = 0.35,
    [string]$Save = "",
    [switch]$Fullscreen
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$cli = @(
    "src\kasir_kulkas.py", "--detector", "yolo", "--model", $Model,
    "--menu", $Menu, "--source", $Source, "--conf", $Conf
)
if ($PayImage) { $cli += @("--pay-image", $PayImage) }
if ($Save) { $cli += @("--save", $Save) }
if ($Fullscreen) { $cli += "--fullscreen" }
& .venv\Scripts\python.exe @cli
exit $LASTEXITCODE
