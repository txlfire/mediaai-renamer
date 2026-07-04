param(
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8970
)

# Purpose: start FastAPI backend and Vite frontend for local/LAN development.
# Flow: validate dependencies -> stop old services -> create logs -> start services -> write PID files.
$ErrorActionPreference = "Stop"

function Normalize-ProcessPathVariable {
    # Avoid Node/npm lookup issues when both Path and PATH exist in the process environment.
    $ProcessEnv = [Environment]::GetEnvironmentVariables("Process")
    if ($ProcessEnv.Contains("Path") -and $ProcessEnv.Contains("PATH")) {
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    }
}

function Find-NodeExecutable {
    # Prefer node.exe for Windows shells, then fall back to node.
    $Node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($Node) {
        return $Node.Source
    }

    $Node = Get-Command node -ErrorAction SilentlyContinue
    if ($Node) {
        return $Node.Source
    }

    throw "Node.js was not found in PATH. Install Node.js, then run npm install."
}

function Assert-CommandAvailable {
    param(
        [string]$Name,
        [string]$Hint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $Hint"
    }
}

function Assert-FileExists {
    param(
        [string]$Path,
        [string]$Hint
    )

    if (-not (Test-Path $Path)) {
        throw "$Path was not found. $Hint"
    }
}

function Stop-PortProcess {
    param([int]$Port)

    # Clear target ports before startup to avoid stale uvicorn/vite listeners.
    $Connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    $ProcessIds = $Connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ProcessId in $ProcessIds) {
        if ($ProcessId -and $ProcessId -ne $PID) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-DevDependencies {
    param([string]$RootPath)

    # Development services require Node, npm, Python venv, backend deps, and Vite.
    Assert-CommandAvailable -Name "npm" -Hint "Install Node.js/npm and reopen the terminal."
    $script:Node = Find-NodeExecutable

    $Python = Join-Path $RootPath ".venv\Scripts\python.exe"
    Assert-FileExists -Path $Python -Hint "Create the virtual environment and install backend dependencies: python -m venv .venv; .\.venv\Scripts\pip.exe install -r backend\requirements.txt"
    Assert-FileExists -Path (Join-Path $RootPath "backend\app\main.py") -Hint "Run this script from the project root."
    Assert-FileExists -Path (Join-Path $RootPath "backend\requirements.txt") -Hint "Backend dependency manifest is missing."
    Assert-FileExists -Path (Join-Path $RootPath "node_modules\vite\bin\vite.js") -Hint "Frontend dependencies are missing. Run npm install."

    & $Python -c "import uvicorn, fastapi" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Backend Python dependencies are incomplete. Run .\.venv\Scripts\pip.exe install -r backend\requirements.txt"
    }

    return $Python
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

# Resolve the project root, validate dependencies, and reuse the stop script for cleanup.
Normalize-ProcessPathVariable
$Python = Assert-DevDependencies -RootPath $Root
& (Join-Path $Root "scripts\stop-dev-lan.ps1") -FrontendPort $FrontendPort -BackendPort $BackendPort

# Store dev logs and PID files under .codex/run-logs for diagnostics and cleanup.
$LogDir = Join-Path $Root ".codex\run-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$BackendOut = Join-Path $LogDir "backend-uvicorn.out.log"
$BackendErr = Join-Path $LogDir "backend-uvicorn.err.log"
$FrontendOut = Join-Path $LogDir "frontend-vite.out.log"
$FrontendErr = Join-Path $LogDir "frontend-vite.err.log"

$env:PYTHONPATH = "backend"
$env:CI = "true"

# Start frontend first by serving the built frontend and proxying /api to backend.
$Frontend = $null
$FrontendDist = "$Root\frontend\dist\index.html"
if (-not (Test-Path $FrontendDist)) {
    throw "Frontend dist was not found. Run npm.cmd run frontend:build first."
}
$FrontendCommand = "start `"MediaAI Frontend`" /min `"$Python`" scripts/serve-frontend.py --host 0.0.0.0 --port $FrontendPort --backend http://127.0.0.1:$BackendPort --dist frontend/dist"
& cmd.exe /c "$FrontendCommand"
if ($LASTEXITCODE -ne 0) {
    throw "Frontend start command failed with code $LASTEXITCODE."
}
Start-Sleep -Seconds 1
$FrontendProcessId = $null
$FrontendConnection = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($FrontendConnection) {
    $FrontendProcessId = $FrontendConnection.OwningProcess
}

# Start backend in a hidden background process with split stdout/stderr logs.
$Backend = Start-Process -FilePath $Python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$BackendPort", "--reload", "--app-dir", "backend") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr -PassThru

# Save PID files so the stop script can terminate the exact processes first.
Set-Content -Path (Join-Path $LogDir "backend.pid") -Value $Backend.Id -Encoding ascii
if ($FrontendProcessId) {
    Set-Content -Path (Join-Path $LogDir "frontend.pid") -Value $FrontendProcessId -Encoding ascii
}

Write-Host "Backend started. PID: $($Backend.Id), URL: http://127.0.0.1:$BackendPort/api/health"
if ($FrontendProcessId) {
    Write-Host "Frontend started. PID: $FrontendProcessId, URL: http://127.0.0.1:$FrontendPort"
} else {
    Write-Host "Frontend start command issued. PID unavailable, URL: http://127.0.0.1:$FrontendPort"
}
Write-Host "LAN URL: http://<this-machine-lan-ip>:$FrontendPort"
Write-Host "Logs: $LogDir"
