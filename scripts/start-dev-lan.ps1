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

function Find-NodeExecutable {
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

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Normalize-ProcessPathVariable
$Python = Assert-DevDependencies -RootPath $Root
& (Join-Path $Root "scripts\stop-dev-lan.ps1") -FrontendPort $FrontendPort -BackendPort $BackendPort

$LogDir = Join-Path $Root ".codex\run-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$BackendOut = Join-Path $LogDir "backend-uvicorn.out.log"
$BackendErr = Join-Path $LogDir "backend-uvicorn.err.log"
$FrontendOut = Join-Path $LogDir "frontend-vite.out.log"
$FrontendErr = Join-Path $LogDir "frontend-vite.err.log"

$env:PYTHONPATH = "backend"
$env:CI = "true"

$Backend = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$BackendPort", "--reload", "--app-dir", "backend") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendOut `
    -RedirectStandardError $BackendErr `
    -PassThru

$ViteEntry = Join-Path $Root "node_modules\vite\bin\vite.js"
$FrontendArguments = @(
    $ViteEntry,
    "--host",
    "0.0.0.0",
    "--port",
    "$FrontendPort",
    "--config",
    "frontend/vite.config.ts"
)

$Frontend = Start-Process `
    -FilePath $Node `
    -ArgumentList $FrontendArguments `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $FrontendOut `
    -RedirectStandardError $FrontendErr `
    -PassThru

Set-Content -Path (Join-Path $LogDir "backend.pid") -Value $Backend.Id -Encoding ascii
Set-Content -Path (Join-Path $LogDir "frontend.pid") -Value $Frontend.Id -Encoding ascii

Write-Host "Backend started. PID: $($Backend.Id), URL: http://127.0.0.1:$BackendPort/api/health"
Write-Host "Frontend started. PID: $($Frontend.Id), URL: http://127.0.0.1:$FrontendPort"
Write-Host "LAN URL: http://<this-machine-lan-ip>:$FrontendPort"
Write-Host "Logs: $LogDir"
