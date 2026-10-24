param(
    [string]$Source = "0",
    [string]$Model = "",
    [string]$Menu = "",
    [double]$Conf = 0.45,
    [double]$MinArea = 0.01,
    [int]$MinConfirm = 3,
    [double]$ConfirmSec = 0.35,
    [int]$Stabil = 10,
    [int]$Imgsz = 640,
    [string]$Device = "auto",
    [int]$DetEvery = 1,
    [string]$Ocr = "auto",
    [string]$Save = "",
    [switch]$Fullscreen
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$args = @(
    "src\kasir_kulkas.py", "--source", $Source, "--conf", $Conf, "--stabil", $Stabil,
    "--imgsz", $Imgsz, "--device", $Device, "--det-every", $DetEvery, "--ocr", $Ocr,
    "--min-area", $MinArea, "--min-confirm", $MinConfirm, "--confirm-sec", $ConfirmSec
)
if ($Model) { $args += @("--model", $Model) }
if ($Menu) { $args += @("--menu", $Menu) }
if ($Save) { $args += @("--save", $Save) }
if ($Fullscreen) { $args += "--fullscreen" }
& .venv\Scripts\python.exe @args
