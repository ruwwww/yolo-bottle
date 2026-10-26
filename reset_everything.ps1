<#
.SYNOPSIS
    Reset Total Sistem Kasir Kulkas (Reset Everything ke Titik Nol).

.DESCRIPTION
    Script ini membersihkan database SQLite, transaksi.csv, mutasi.jsonl,
    dan foto di folder pelanggaran/. Data lama otomatis diarsipkan secara aman.

.PARAMETER Kill
    Hentikan background proses Python (kasir / webhook server).

.PARAMETER Hard
    Hapus bersih langsung tanpa membuat backup folder di archive/.

.EXAMPLE
    .\reset_everything.ps1
    .\reset_everything.ps1 -Kill
    .\reset_everything.ps1 -Hard
#>

param(
    [switch]$Kill,
    [switch]$Hard
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pythonExe = ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python.exe"
}

$argsList = @("scripts\reset_everything.py")
if ($Kill) {
    $argsList += "--kill"
}
if ($Hard) {
    $argsList += "--hard"
}

& $pythonExe @argsList
exit $LASTEXITCODE
