param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 5173,
    [switch]$Background
)

# Purpose: start only the Vite frontend dev server, in foreground or background mode.
# Flow: resolve project root -> find Node/Vite -> build Vite args -> start by mode.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Normalize-ProcessPathVariable {
    # Avoid Node lookup issues when both Path and PATH exist in the process environment.
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

    throw "Node.js executable was not found in PATH."
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Normalize-ProcessPathVariable
$env:CI = "true"

# Call the local Vite entry so the project-pinned frontend dependencies are used.
$Node = Find-NodeExecutable
$ViteEntry = Join-Path $Root "node_modules\vite\bin\vite.js"
if (-not (Test-Path $ViteEntry)) {
    throw "Vite entry was not found. Run npm install first."
}

$Arguments = @(
    $ViteEntry,
    "--host",
    $HostName,
    "--port",
    "$Port",
    "--config",
    "frontend/vite.config.ts"
)

if ($Background) {
    # Background mode writes logs and keeps the terminal available.
    $LogDir = Join-Path $Root ".codex\run-logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $OutLog = Join-Path $LogDir "frontend-vite.out.log"
    $ErrLog = Join-Path $LogDir "frontend-vite.err.log"

    $Process = Start-Process `
        -FilePath $Node `
        -ArgumentList $Arguments `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru

    Write-Host "Frontend dev server started in background."
    Write-Host "PID: $($Process.Id)"
    Write-Host "Local: http://127.0.0.1:$Port"
    Write-Host "LAN: http://<this-machine-lan-ip>:$Port"
    Write-Host "Logs: $OutLog"
    exit 0
}

# Foreground mode streams Vite output to the current terminal for manual debugging.
& $Node @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Frontend dev server exited with code $LASTEXITCODE."
}
