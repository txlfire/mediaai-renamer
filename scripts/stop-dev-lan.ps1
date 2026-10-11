param(
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8970
)

# Purpose: stop FastAPI backend and Vite frontend development services.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Remove-PidFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Stop-ProjectDevProcesses {
    param([string]$RootPath, [string]$LogPath)

    # Match only vite/uvicorn processes for this project root to avoid other projects.
    $EscapedRoot = [regex]::Escape($RootPath)
    $AllProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $Processes = @($AllProcesses | Where-Object {
        $_.CommandLine -and
        $_.Name -in @("python.exe", "node.exe") -and
        $_.ProcessId -ne $PID -and
        (
            $_.CommandLine -match $EscapedRoot -and
            ($_.CommandLine -match "vite\.js" -or $_.CommandLine -match "uvicorn app\.main:app")
        )
    })
    $Selected = @{}
    foreach ($Process in $Processes) {
        $Selected[[int]$Process.ProcessId] = 0
    }
    foreach ($Name in @("frontend.pid", "backend.pid")) {
        $PidPath = Join-Path $LogPath $Name
        if (-not (Test-Path -LiteralPath $PidPath)) { continue }
        $RawPid = Get-Content -LiteralPath $PidPath -ErrorAction Stop | Select-Object -First 1
        if ($RawPid -notmatch '^\d+$') { continue }
        $Candidate = $AllProcesses | Where-Object { $_.ProcessId -eq [int]$RawPid } | Select-Object -First 1
        if (-not $Candidate -or -not $Candidate.CommandLine) { continue }
        $WrittenAt = (Get-Item -LiteralPath $PidPath).LastWriteTimeUtc
        $CreatedAt = $Candidate.CreationDate.ToUniversalTime()
        if ($CreatedAt -gt $WrittenAt -or ($WrittenAt - $CreatedAt).TotalSeconds -gt 15) { continue }
        if ($Candidate.CommandLine -match "vite[\\/]bin[\\/]vite\.js" -and
            $Candidate.CommandLine -match "frontend[\\/]vite\.config\.ts") {
            $Selected[[int]$Candidate.ProcessId] = 0
        }
    }
    do {
        $Added = $false
        foreach ($Process in $AllProcesses) {
            $ParentId = [int]$Process.ParentProcessId
            $ProcessId = [int]$Process.ProcessId
            if ($Selected.ContainsKey($ParentId) -and -not $Selected.ContainsKey($ProcessId)) {
                $Selected[$ProcessId] = $Selected[$ParentId] + 1
                $Added = $true
            }
        }
    } while ($Added)

    foreach ($ProcessId in @($Selected.Keys | Sort-Object { $Selected[$_] } -Descending)) {
        if ($ProcessId -ne $PID) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $Root ".codex\run-logs"

Stop-ProjectDevProcesses -RootPath $Root -LogPath $LogDir
Remove-PidFile -Path (Join-Path $LogDir "frontend.pid")
Remove-PidFile -Path (Join-Path $LogDir "backend.pid")

# Wait briefly for port release before the final listening-state check.
Start-Sleep -Seconds 1
$Listening = Get-NetTCPConnection -LocalPort $FrontendPort,$BackendPort -State Listen -ErrorAction SilentlyContinue
if ($Listening) {
    Write-Warning "Some ports are still listening. Unknown owners were not stopped; inspect ownership or choose different ports:"
    $Listening | Select-Object LocalAddress,LocalPort,OwningProcess,State | Format-Table -AutoSize
    exit 1
}

Write-Host "Development services stopped."
