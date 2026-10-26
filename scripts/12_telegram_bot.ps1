param(
    [switch]$Test,
    [switch]$Listen,
    [switch]$Info,
    [string]$SetChat = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$cli = @("src\telegram_notify.py")

if ($Test) {
    $cli += @("--test")
} elseif ($Listen) {
    $cli += @("--listen")
} elseif ($SetChat) {
    $cli += @("--set-chat", $SetChat)
} else {
    $cli += @("--info")
}

& .venv\Scripts\python.exe @cli
exit $LASTEXITCODE
