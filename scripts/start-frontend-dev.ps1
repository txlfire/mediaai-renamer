param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 5173,
    [switch]$Background
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

    throw "Node.js executable was not found in PATH."
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Normalize-ProcessPathVariable
$env:CI = "true"

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

& $Node @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Frontend dev server exited with code $LASTEXITCODE."
}
