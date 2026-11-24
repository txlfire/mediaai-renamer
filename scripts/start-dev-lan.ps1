param(
    [Nullable[int]]$FrontendPort,
    [Nullable[int]]$BackendPort
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

function Wait-DevHttpReady {
    param([string]$Url, [int]$TimeoutSeconds = 30)

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) {
                return
            }
        } catch {
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $Deadline)
    throw "Development service readiness timed out: $Url. Check .codex/run-logs."
}

function Assert-DevDependencies {
    param([string]$RootPath)

    # Development services require Node, npm, Python venv, backend deps, and Vite.
    Assert-CommandAvailable -Name "npm" -Hint "Install Node.js/npm and reopen the terminal."
    $script:Node = Find-NodeExecutable

    $Python = Join-Path $RootPath ".venv\Scripts\python.exe"
    Assert-FileExists -Path $Python -Hint "Create the virtual environment and install backend dependencies: python -m venv .venv; .\.venv\Scripts\pip.exe install --require-hashes -r backend\requirements.lock"
    Assert-FileExists -Path (Join-Path $RootPath "backend\app\main.py") -Hint "Run this script from the project root."
    Assert-FileExists -Path (Join-Path $RootPath "backend\requirements.txt") -Hint "Backend dependency manifest is missing."
    Assert-FileExists -Path (Join-Path $RootPath "backend\requirements.lock") -Hint "Backend dependency lock is missing."
    Assert-FileExists -Path (Join-Path $RootPath "node_modules\vite\bin\vite.js") -Hint "Frontend dependencies are missing. Run npm ci."

    & $Python -c "import uvicorn, fastapi" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Backend Python dependencies are incomplete. Run .\.venv\Scripts\pip.exe install --require-hashes -r backend\requirements.lock"
    }

    return $Python
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
. (Join-Path $PSScriptRoot "dev-port-config.ps1")

# Resolve the project root, validate dependencies, and reuse the stop script for cleanup.
Normalize-ProcessPathVariable
$Python = Assert-DevDependencies -RootPath $Root
$Ports = Get-ConfiguredDevPorts -RootPath $Root -FrontendPort $FrontendPort -BackendPort $BackendPort
$FrontendPort = $Ports.frontendPort
$BackendPort = $Ports.backendPort
& (Join-Path $Root "scripts\stop-dev-lan.ps1") -OwnedOnly
if ($LASTEXITCODE -ne 0) { throw "Could not stop the previous development services." }

$PortReleaseDeadline = (Get-Date).AddSeconds(10)
do {
    $RemainingListeners = Get-NetTCPConnection -LocalPort $FrontendPort,$BackendPort -State Listen -ErrorAction SilentlyContinue
    if (-not $RemainingListeners) {
        break
    }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $PortReleaseDeadline)
if ($RemainingListeners) {
    throw "Development ports are still occupied. Startup aborted; no new services were launched."
}
$null = Get-ConfiguredDevPorts -RootPath $Root -FrontendPort $FrontendPort -BackendPort $BackendPort -CheckBind

# Store dev logs and PID files under .codex/run-logs for diagnostics and cleanup.
$LogDir = Join-Path $Root ".codex\run-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$BackendOut = Join-Path $LogDir "backend-uvicorn.out.log"
$BackendErr = Join-Path $LogDir "backend-uvicorn.err.log"
$FrontendOut = Join-Path $LogDir "frontend-vite.out.log"
$FrontendErr = Join-Path $LogDir "frontend-vite.err.log"

$env:PYTHONPATH = "backend"
$env:CI = "true"
$env:VITE_BACKEND_URL = "http://127.0.0.1:$BackendPort"
$env:MEDIAAI_DEV_FRONTEND_PORT = "$FrontendPort"
$env:MEDIAAI_DEV_BACKEND_PORT = "$BackendPort"
$BindHost = if ($env:MEDIAAI_DEV_LOOPBACK_ONLY -eq "1") { "127.0.0.1" } else { "0.0.0.0" }

# Start backend in a hidden background process with split stdout/stderr logs.
$Backend = Start-Process -FilePath $Python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$BackendPort", "--reload", "--reload-dir", "backend/app", "--app-dir", "backend") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr -PassThru

# Start frontend in a hidden background process by invoking the local Vite entry directly.
$Frontend = Start-Process -FilePath $script:Node -ArgumentList @("node_modules\vite\bin\vite.js", "--host", $BindHost, "--port", "$FrontendPort", "--strictPort", "--config", "frontend/vite.config.ts") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $FrontendOut -RedirectStandardError $FrontendErr -PassThru

# Save PID files so the stop script can terminate the exact processes first.
Set-Content -Path (Join-Path $LogDir "backend.pid") -Value $Backend.Id -Encoding ascii
Set-Content -Path (Join-Path $LogDir "frontend.pid") -Value $Frontend.Id -Encoding ascii

Wait-DevHttpReady -Url "http://127.0.0.1:$BackendPort/api/health"
Wait-DevHttpReady -Url "http://127.0.0.1:$FrontendPort/"
Wait-DevHttpReady -Url "http://127.0.0.1:$FrontendPort/api/health"
$Ports | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $LogDir "dev-ports.json") -Encoding ascii

Write-Host "Backend started. PID: $($Backend.Id), URL: http://127.0.0.1:$BackendPort/api/health"
Write-Host "Frontend started. PID: $($Frontend.Id), URL: http://127.0.0.1:$FrontendPort"
if ($BindHost -eq "0.0.0.0") { Write-Host "LAN URL: http://<this-machine-lan-ip>:$FrontendPort" }
Write-Host "Logs: $LogDir"
