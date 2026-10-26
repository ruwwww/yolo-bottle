param(
    [string]$SshHost = "kuroko",
    [int]$Port = 7860,
    [int]$ApiPort = 8090,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$Log
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
scp -q -o BatchMode=yes -o ConnectTimeout=15 remote\moondream_ui.py "${SshHost}:yolo-bottle/moondream_ui.py"
scp -q -o BatchMode=yes -o ConnectTimeout=15 remote\moondream_server.py "${SshHost}:yolo-bottle/moondream_server.py"
if ($Stop -or $Restart) {
    ssh $SshHost "pkill -f 'moondream_ui.py' || true"
    if ($Stop) { Write-Host "UI moondream dihentikan"; if ($Log) { ssh $SshHost "tail -n 20 ~/md_setup/ui.log" }; exit 0 }
    Start-Sleep -Seconds 1
}
ssh $SshHost "cd ~/yolo-bottle && (MD_API=http://127.0.0.1:$ApiPort GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=$Port nohup ~/.conda/envs/ai/bin/python moondream_ui.py > ~/md_setup/ui.log 2>&1 &)"
Write-Host "menunggu UI siap di :$Port..."
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $raw = ssh $SshHost "curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:$Port/"
    if ($raw -match '^200$') {
        Write-Host "OK: UI http://${SshHost}:$Port"
        if ($Log) { ssh $SshHost "tail -n 20 ~/md_setup/ui.log" }
        exit 0
    }
}
Write-Host "gagal: UI tidak merespon di ${SshHost}:$Port"
ssh $SshHost "tail -n 30 ~/md_setup/ui.log"
exit 1
