function Get-ConfiguredDevPorts {
    param(
        [string]$RootPath,
        [Nullable[int]]$FrontendPort,
        [Nullable[int]]$BackendPort,
        [switch]$CheckBind
    )

    $Python = Join-Path $RootPath ".venv\Scripts\python.exe"
    $Arguments = @("-m", "app.dev_ports")
    if ($null -ne $FrontendPort) { $Arguments += @("--frontend", "$FrontendPort") }
    if ($null -ne $BackendPort) { $Arguments += @("--backend", "$BackendPort") }
    if ($CheckBind) { $Arguments += "--check-bind" }
    $OldPythonPath = $env:PYTHONPATH
    Push-Location -LiteralPath $RootPath
    try {
        $env:PYTHONPATH = Join-Path $RootPath "backend"
        $Result = & $Python @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Could not resolve the local development ports." }
        return $Result | ConvertFrom-Json
    } finally {
        $env:PYTHONPATH = $OldPythonPath
        Pop-Location
    }
}

function Get-LastDevPorts {
    param([string]$RootPath)

    # 只读取上次监听记录 (不参与下次配置解析).
    $Path = Join-Path $RootPath ".codex\run-logs\dev-ports.json"
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $Ports = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        if (($Ports.frontendPort -is [int] -or $Ports.frontendPort -is [long]) -and
            ($Ports.backendPort -is [int] -or $Ports.backendPort -is [long]) -and
            $Ports.frontendPort -ge 1024 -and $Ports.frontendPort -le 65535 -and
            $Ports.backendPort -ge 1024 -and $Ports.backendPort -le 65535 -and
            $Ports.frontendPort -ne $Ports.backendPort) { return $Ports }
    } catch {
        Write-Warning "Ignoring an invalid local process port record."
    }
    return $null
}
