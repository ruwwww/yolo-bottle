param(
    [string]$SshHost = "100.91.215.116",
    [int]$Port = 8090,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$Log
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
scp -q -o BatchMode=yes -o ConnectTimeout=15 remote\moondream_server.py "${SshHost}:yolo-bottle/moondream_server.py"
if ($Stop -or $Restart) {
    ssh $SshHost "pkill -f 'uvicorn moondream_server' || true"
    if ($Stop) { Write-Host "server moondream dihentikan"; if ($Log) { ssh $SshHost "tail -n 20 ~/md_setup/server.log" }; exit 0 }
    Start-Sleep -Seconds 2
}
ssh $SshHost "cd ~/yolo-bottle && (nohup ~/.conda/envs/ai/bin/python -m uvicorn moondream_server:app --host 0.0.0.0 --port $Port > ~/md_setup/server.log 2>&1 &)"
Write-Host "menunggu model siap (load ~35 s)..."
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-RestMethod -Uri "http://${SshHost}:$Port/health" -TimeoutSec 2
        Write-Host "OK: model=$($h.model) uptime=$($h.uptime)s"
        if ($Log) { ssh $SshHost "tail -n 20 ~/md_setup/server.log" }
        exit 0
    } catch { }
}
Write-Host "gagal: server tidak merespon di ${SshHost}:$Port"
ssh $SshHost "tail -n 30 ~/md_setup/server.log"
exit 1
