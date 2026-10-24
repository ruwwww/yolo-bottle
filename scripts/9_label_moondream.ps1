param(
    [string]$Source = "dataset\capture",
    [string]$Out = "dataset\raw",
    [string]$Prompts = "",
    [int]$Limit = 0,
    [int]$Resize = 640,
    [switch]$SkipEmpty,
    [switch]$KeepRemote,
    [switch]$KeepServer,
    [switch]$DownloadOnly,
    [switch]$NoSplit
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$cli = @(
    "src\label_remote.py", "--source", $Source, "--out", $Out,
    "--resize", $Resize
)
if ($Prompts) { $cli += @("--prompts", $Prompts) }
if ($Limit -gt 0) { $cli += @("--limit", $Limit) }
if ($SkipEmpty) { $cli += "--skip-empty" }
if ($KeepRemote) { $cli += "--keep-remote" }
if ($KeepServer) { $cli += "--keep-server" }
if ($DownloadOnly) { $cli += "--download-only" }
& .venv\Scripts\python.exe @cli
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $NoSplit) {
    & .venv\Scripts\python.exe src\split_dataset.py --raw $Out --keep-empty
}
