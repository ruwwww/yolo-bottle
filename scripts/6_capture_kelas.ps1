param(
    [string]$Source = "0",
    [string]$Classes = "bottle,ballpoint_box,computer_mouse,teh_pucuk,golda",
    [string]$Prompts = "",
    [string]$Class = "",
    [double]$AutoSave = 0,
    [int]$Max = 0,
    [string]$Out = "dataset\capture",
    [switch]$NoWindow
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$cli = @(
    "src\collect_dataset.py", "--source", $Source, "--classes", $Classes,
    "--out", $Out, "--max", $Max, "--auto-save", $AutoSave
)
if ($Prompts) { $cli += @("--prompts", $Prompts) }
if ($Class) { $cli += @("--class", $Class) }
if ($NoWindow) { $cli += "--no-window" }
& .venv\Scripts\python.exe @cli
exit $LASTEXITCODE
