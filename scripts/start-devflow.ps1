param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $env:LOCALAPPDATA "DevFlow\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-Listening([int]$port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(300)
        if ($ok -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Wait-Listening([int]$port, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Listening $port) { return $true }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

$python = "C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe"
$backendPort = 8010
$frontendPort = 5173
if (-not (Test-Path $python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    Write-Host "[DevFlow] Python not found. Create the agent-dev env first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Host "[DevFlow] First run: installing frontend dependencies ..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "frontend")
    npm install
    Pop-Location
}

if (Test-Listening $backendPort) {
    Write-Host "[DevFlow] Backend already running on :$backendPort" -ForegroundColor Yellow
} else {
    Write-Host "[DevFlow] Starting backend ..." -ForegroundColor Cyan
    Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$backendPort" `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput (Join-Path $logDir "backend.log") `
        -RedirectStandardError (Join-Path $logDir "backend.err.log") `
        -WindowStyle Hidden
}

if (Test-Listening $frontendPort) {
    Write-Host "[DevFlow] Frontend already running on :$frontendPort" -ForegroundColor Yellow
} else {
    Write-Host "[DevFlow] Starting frontend ..." -ForegroundColor Cyan
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "npm run dev" `
        -WorkingDirectory (Join-Path $root "frontend") `
        -RedirectStandardOutput (Join-Path $logDir "frontend.log") `
        -RedirectStandardError (Join-Path $logDir "frontend.err.log") `
        -WindowStyle Hidden
}

$backendOk = Wait-Listening $backendPort 45
$frontendOk = Wait-Listening $frontendPort 120

if ($backendOk) {
    Write-Host "[DevFlow] Backend ready  -> http://localhost:$backendPort/health" -ForegroundColor Green
} else {
    Write-Host "[DevFlow] Backend NOT ready on :$backendPort" -ForegroundColor Red
    Write-Host "          Log: $(Join-Path $logDir 'backend.err.log')" -ForegroundColor DarkGray
}
if ($frontendOk) {
    Write-Host "[DevFlow] Frontend ready -> http://localhost:$frontendPort" -ForegroundColor Green
} else {
    Write-Host "[DevFlow] Frontend NOT ready on :$frontendPort" -ForegroundColor Red
    Write-Host "          Log: $(Join-Path $logDir 'frontend.err.log')" -ForegroundColor DarkGray
}
Write-Host "[DevFlow] Logs: $logDir" -ForegroundColor DarkGray

if ($backendOk -and $frontendOk -and -not $NoBrowser) {
    Start-Process "http://localhost:$frontendPort"
    Write-Host "[DevFlow] Browser opened. Double-click stop-devflow.bat to shut down." -ForegroundColor Cyan
}

if (-not ($backendOk -and $frontendOk)) { exit 1 }
exit 0
