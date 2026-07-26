# 用途：在 Windows 中启动隔离的 HTTPS WebDAV 容器并执行真实协议集成测试。
# 关键步骤：检查 Docker -> 清理固定测试目录 -> 启动容器 -> 等待健康 -> 执行测试 -> 销毁容器和临时数据。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composeDir = [IO.Path]::GetFullPath((Join-Path $root "tests\integration\webdav"))
$composeFile = Join-Path $composeDir "docker-compose.yml"
$tempRoot = [IO.Path]::GetFullPath((Join-Path $composeDir ".tmp"))
$expectedPrefix = $composeDir.TrimEnd("\") + "\"

if (-not $tempRoot.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "WebDAV 测试临时目录不在预期工作区内：$tempRoot"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker，请在安装 Docker 的 Windows、fnOS 或 GitHub Actions 环境执行。"
}

docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "当前 Docker 未提供 compose 子命令。"
}

$env:MEDIAAI_WEBDAV_TEST_USERNAME = if ($env:MEDIAAI_WEBDAV_TEST_USERNAME) {
    $env:MEDIAAI_WEBDAV_TEST_USERNAME
} else {
    "mediaai-test"
}
$env:MEDIAAI_WEBDAV_TEST_PASSWORD = if ($env:MEDIAAI_WEBDAV_TEST_PASSWORD) {
    $env:MEDIAAI_WEBDAV_TEST_PASSWORD
} else {
    "mediaai-test-only-password"
}

if (Test-Path -LiteralPath $tempRoot) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $tempRoot "certs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "data") -Force | Out-Null

Push-Location $root
try {
    docker compose -f $composeFile up -d --build
    if ($LASTEXITCODE -ne 0) {
        throw "WebDAV 测试容器启动失败。"
    }

    $healthy = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        $status = docker inspect --format "{{.State.Health.Status}}" mediaai-webdav-test 2>$null
        if ($LASTEXITCODE -eq 0 -and $status -eq "healthy") {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) {
        docker compose -f $composeFile logs --no-color webdav
        throw "WebDAV 测试容器未在 60 秒内进入健康状态。"
    }

    $env:MEDIAAI_WEBDAV_INTEGRATION = "1"
    $env:MEDIAAI_WEBDAV_TEST_URL = "https://localhost:9443"
    $env:MEDIAAI_WEBDAV_TEST_CA_CERT = Join-Path $tempRoot "certs\ca.crt"
    $env:PYTHONPATH = "backend"

    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "未找到项目虚拟环境 Python：$python"
    }
    & $python -m unittest backend.tests.integration.test_webdav_e2e -v
    if ($LASTEXITCODE -ne 0) {
        throw "WebDAV 协议集成测试失败。"
    }
}
finally {
    docker compose -f $composeFile down -v --remove-orphans
    Pop-Location
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
