param(
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8970
)

# Purpose: stop FastAPI backend and Vite frontend development services.
# Flow: stop by PID files -> clean project processes -> clean ports -> verify release.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Stop-PidFile {
    param([string]$Path)

    # PID files are the most precise cleanup path; remove them after use.
    if (-not (Test-Path $Path)) {
        return
    }

    $RawPid = (Get-Content -Path $Path -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($RawPid -match '^\d+$') {
        Stop-Process -Id ([int]$RawPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Path $Path -Force -ErrorAction SilentlyContinue
}

function Stop-PortProcess {
    param([int]$Port)

    # Fallback cleanup for processes still listening after PID-file cleanup.
    $Connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    $ProcessIds = $Connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ProcessId in $ProcessIds) {
        if ($ProcessId -and $ProcessId -ne $PID) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-ProjectDevProcesses {
    param([string]$RootPath)

    # Match only vite/uvicorn processes for this project root to avoid other projects.
    $EscapedRoot = [regex]::Escape($RootPath)
    $Processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.ProcessId -ne $PID -and
        (
            ($_.CommandLine -match $EscapedRoot -and ($_.CommandLine -match "vite\.js" -or $_.CommandLine -match "uvicorn app\.main:app")) -or
            ($_.CommandLine -match "uvicorn app\.main:app" -and $_.CommandLine -match "--port $BackendPort")
        )
    }

    foreach ($Process in $Processes) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $Root ".codex\run-logs"

# Stop by PID first, then fall back to project process and port cleanup.
Stop-PidFile -Path (Join-Path $LogDir "frontend.pid")
Stop-PidFile -Path (Join-Path $LogDir "backend.pid")
Stop-ProjectDevProcesses -RootPath $Root
Stop-PortProcess -Port $FrontendPort
Stop-PortProcess -Port $BackendPort

# Wait briefly for port release before the final listening-state check.
Start-Sleep -Seconds 1
$Listening = Get-NetTCPConnection -LocalPort $FrontendPort,$BackendPort -State Listen -ErrorAction SilentlyContinue
if ($Listening) {
    Write-Warning "Some ports are still listening. You may need an elevated terminal to stop them:"
    $Listening | Select-Object LocalAddress,LocalPort,OwningProcess,State | Format-Table -AutoSize
    exit 1
}

Write-Host "Development services stopped."
