param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "status",
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8970
)

# 用途：Windows 一键管理本地开发服务。
# 关键步骤：根据 Action 判断动作 -> 调用现有启停脚本 -> 输出前后端状态。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-HttpEndpoint {
    param([string]$Url)

    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3 -ErrorAction Stop
        $StatusCode = [int]$Response.StatusCode
        return ($StatusCode -ge 200 -and $StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Test-TcpEndpoint {
    param(
        [string]$HostName,
        [int]$Port
    )

    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $ConnectTask = $Client.ConnectAsync($HostName, $Port)
        if (-not $ConnectTask.Wait(1000)) {
            return $false
        }
        return $Client.Connected
    } catch {
        return $false
    } finally {
        $Client.Dispose()
    }
}

function Show-ServiceStatus {
    param(
        [int]$StatusFrontendPort,
        [int]$StatusBackendPort
    )

    # 优先使用 HTTP 探活，避免端口监听查询因权限或地址族差异误报。
    $BackendUrl = "http://127.0.0.1:{0}/api/health" -f $StatusBackendPort
    $FrontendUrl = "http://127.0.0.1:{0}" -f $StatusFrontendPort
    $BackendListening = (Test-HttpEndpoint -Url $BackendUrl) -or (Test-TcpEndpoint -HostName "127.0.0.1" -Port $StatusBackendPort)
    $FrontendListening = (Test-HttpEndpoint -Url $FrontendUrl) -or (Test-TcpEndpoint -HostName "127.0.0.1" -Port $StatusFrontendPort)
    $BackendDisplayUrl = "http://127.0.0.1:{0}/api/health" -f $StatusBackendPort
    $FrontendDisplayUrl = "http://127.0.0.1:{0}" -f $StatusFrontendPort

    if ($BackendListening) {
        Write-Host "Backend: running, $BackendDisplayUrl"
    } else {
        Write-Host "Backend: stopped"
    }

    if ($FrontendListening) {
        Write-Host "Frontend: running, $FrontendDisplayUrl"
    } else {
        Write-Host "Frontend: stopped"
    }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$StartScript = Join-Path $Root "scripts\start-dev-lan.ps1"
$StopScript = Join-Path $Root "scripts\stop-dev-lan.ps1"

switch ($Action) {
    "start" {
        & $StartScript -FrontendPort $FrontendPort -BackendPort $BackendPort
        Show-ServiceStatus -StatusFrontendPort $FrontendPort -StatusBackendPort $BackendPort
    }
    "stop" {
        & $StopScript -FrontendPort $FrontendPort -BackendPort $BackendPort
        Show-ServiceStatus -StatusFrontendPort $FrontendPort -StatusBackendPort $BackendPort
    }
    "restart" {
        & $StopScript -FrontendPort $FrontendPort -BackendPort $BackendPort
        & $StartScript -FrontendPort $FrontendPort -BackendPort $BackendPort
        Show-ServiceStatus -StatusFrontendPort $FrontendPort -StatusBackendPort $BackendPort
    }
    "status" {
        Show-ServiceStatus -StatusFrontendPort $FrontendPort -StatusBackendPort $BackendPort
    }
}
