param(
    [string]$Version = "",
    [string]$Repo = "txlfire/mediaai-renamer",
    [string]$Target = "main",
    [string]$Notes = "",
    [switch]$Publish,
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Package = Get-Content -Raw "package.json" | ConvertFrom-Json
    $Version = $Package.version
}

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
$Artifact = Join-Path $ReleaseDir "mediaai-renamer-frontend-$Tag.zip"
$PackageRoot = Join-Path $ReleaseDir "package-$Tag"

if (-not $SkipBuild) {
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
Copy-Item -Path (Join-Path $DistDir "*") -Destination $PackageRoot -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "config") | Out-Null
Copy-Item -Path (Join-Path $Root "config\config.example.toml") -Destination (Join-Path $PackageRoot "config\config.example.toml") -Force

Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $Artifact -Force
Remove-Item -Path $PackageRoot -Recurse -Force

Write-Host "Release package created: $Artifact"

if ($Publish) {
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
