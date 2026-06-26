param(
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8970
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Normalize-ProcessPathVariable {
    $ProcessEnv = [Environment]::GetEnvironmentVariables("Process")
    if ($ProcessEnv.Contains("Path") -and $ProcessEnv.Contains("PATH")) {
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    }
}

function Stop-PortProcess {
    param([int]$Port)

    $Connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    $ProcessIds = $Connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ProcessId in $ProcessIds) {
        if ($ProcessId -and $ProcessId -ne $PID) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Normalize-ProcessPathVariable
Stop-PortProcess -Port $FrontendPort
Stop-PortProcess -Port $BackendPort

$LogDir = Join-Path $Root ".codex\run-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$BackendOut = Join-Path $LogDir "backend-uvicorn.out.log"
$BackendErr = Join-Path $LogDir "backend-uvicorn.err.log"
$FrontendOut = Join-Path $LogDir "frontend-vite.out.log"
$FrontendErr = Join-Path $LogDir "frontend-vite.err.log"

$env:PYTHONPATH = "backend"
$env:CI = "true"

$Backend = Start-Process `
    -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$BackendPort", "--reload", "--app-dir", "backend") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendOut `
    -RedirectStandardError $BackendErr `
    -PassThru

$FrontendScript = Join-Path $Root "scripts\start-frontend-dev.ps1"
$Frontend = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-ExecutionPolicy", "Bypass", "-File", $FrontendScript, "-HostName", "0.0.0.0", "-Port", "$FrontendPort", "-Background") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Backend started. PID: $($Backend.Id), URL: http://127.0.0.1:$BackendPort/api/health"
Write-Host "Frontend launcher started. PID: $($Frontend.Id), URL: http://127.0.0.1:$FrontendPort"
Write-Host "LAN URL: http://<this-machine-lan-ip>:$FrontendPort"
Write-Host "Logs: $LogDir"
