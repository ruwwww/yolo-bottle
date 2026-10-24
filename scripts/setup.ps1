$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements-ocr.txt
Write-Host "setup selesai. Lanjut: scripts\6_capture_kelas.ps1"
