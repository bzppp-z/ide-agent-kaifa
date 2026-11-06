$ErrorActionPreference = "SilentlyContinue"
$ports = @(8010, 5173)

foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "[DevFlow] Port $port is free" -ForegroundColor DarkGray
        continue
    }
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host ("[DevFlow] Stopping {0} (PID {1}) on port {2}" -f $proc.ProcessName, $proc.Id, $port)
            Stop-Process -Id $proc.Id -Force
        }
    }
    Start-Sleep -Milliseconds 600
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "[DevFlow] Port $port still in use, please close it manually" -ForegroundColor Yellow
    } else {
        Write-Host "[DevFlow] Port $port released" -ForegroundColor Green
    }
}

Write-Host "[DevFlow] Done. You can close the backend/frontend windows." -ForegroundColor Cyan
