param(
    [string]$Version = "",
    [string]$Repo = "txlfire/mediaai-renamer",
    [string]$Target = "main",
    [string]$Notes = "",
    [switch]$Publish,
    [switch]$SkipBuild
)

# Purpose: build the unified frontend/backend release artifact on Windows and optionally publish it.
# Flow: resolve version -> build frontend -> copy backend/dist/example config -> zip -> publish.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    # Wrap native commands so non-zero exit codes stop the release flow.
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Copy-DirectoryTree {
    param(
        [string]$Source,
        [string]$Destination
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy $Source $Destination /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Directory copy failed with exit code ${LASTEXITCODE}: $Source -> $Destination"
    }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

# Use the root package.json version when no version is passed.
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Package = Get-Content -Raw "package.json" | ConvertFrom-Json
    $Version = $Package.version
}

# Normalize release tags to vX.Y.Z, avoiding duplicate v prefixes.
$CleanVersion = $Version.Trim()
if ($CleanVersion.StartsWith("v")) {
    $CleanVersion = $CleanVersion.Substring(1)
}

if ([string]::IsNullOrWhiteSpace($CleanVersion)) {
    throw "Version cannot be empty."
}

$Tag = "v$CleanVersion"
$DistDir = Join-Path $Root "frontend\dist"
$ReleaseDir = Join-Path $Root "releases"
$Artifact = Join-Path $ReleaseDir "mediaai-renamer-$Tag.zip"
$PackageRoot = Join-Path $ReleaseDir "package-$Tag"

if (-not $SkipBuild) {
    # Build frontend by default so the artifact matches current source.
    Invoke-NativeCommand "npm.cmd" @("run", "frontend:build")
}

if (-not (Test-Path $DistDir)) {
    throw "Frontend dist directory not found: $DistDir"
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path $PackageRoot) {
    Remove-Item -Path $PackageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
# 合并后端运行代码和前端构建产物，不打包测试、缓存或本地依赖。
New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "backend") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "frontend-dist") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "config") | Out-Null
[System.IO.File]::Copy(
    (Join-Path $Root "config\config.example.toml"),
    (Join-Path $PackageRoot "config\config.example.toml"),
    $true
)
Copy-DirectoryTree (Join-Path $Root "backend\app") (Join-Path $PackageRoot "backend\app")
Copy-Item -Path (Join-Path $Root "backend\requirements.txt") -Destination (Join-Path $PackageRoot "backend\requirements.txt") -Force
Copy-Item -Path (Join-Path $DistDir "*") -Destination (Join-Path $PackageRoot "frontend-dist") -Recurse -Force

$RequiredPackageFiles = @(
    "backend\app\main.py",
    "backend\requirements.txt",
    "frontend-dist\index.html",
    "config\config.example.toml"
)
foreach ($RelativePath in $RequiredPackageFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $RelativePath))) {
        throw "Required package file is missing before compression: $RelativePath"
    }
}

# Assemble the artifact in a temporary package directory, then clean it.
Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $Artifact -Force
Remove-Item -Path $PackageRoot -Recurse -Force

Write-Host "Release package created: $Artifact"

if ($Publish) {
    # Publish mode requires GitHub CLI and creates or updates the release asset.
    $Gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $Gh) {
        throw "GitHub CLI is not installed or not available in PATH."
    }

    Invoke-NativeCommand "gh" @("auth", "status")

    if ([string]::IsNullOrWhiteSpace($Notes)) {
        $Notes = "MediaAI Renamer $Tag release."
    }

    gh release view $Tag --repo $Repo *> $null
    if ($LASTEXITCODE -eq 0) {
        Invoke-NativeCommand "gh" @("release", "upload", $Tag, $Artifact, "--repo", $Repo, "--clobber")
        Write-Host "Release asset uploaded to existing release: $Tag"
    }
    else {
        Invoke-NativeCommand "gh" @("release", "create", $Tag, $Artifact, "--repo", $Repo, "--target", $Target, "--title", $Tag, "--notes", $Notes, "--latest")
        Write-Host "GitHub release created: $Tag"
    }
}
