[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TargetsPath = (Join-Path $PSScriptRoot "..\deploy.targets.json"),
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$packageId = "MHubRcvr01"
$appId = "MHubRcvr01.MultiHubReceiver"
$appDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..\app")).Path

function Find-TizenTool([string]$Name) {
    foreach ($candidateName in @("$Name.exe", "$Name.bat", $Name)) {
        $fromPath = Get-Command $candidateName -ErrorAction SilentlyContinue
        if ($fromPath) { return $fromPath.Source }
    }
    $candidates = @(
        (Join-Path "C:\tizen-studio\tools" "$Name.exe"),
        (Join-Path "C:\tizen-studio\tools" "$Name.bat")
    )
    if ($Name -eq "tizen") {
        $candidates += "C:\tizen-studio\tools\ide\bin\tizen.bat"
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "Could not find the Tizen $Name CLI. Install Tizen Studio or add it to PATH."
}

function Invoke-TizenCommand([string]$Tool, [string[]]$Arguments, [switch]$AllowFailure) {
    $display = "`"$Tool`" " + ($Arguments | ForEach-Object { "`"$_`"" } | Join-String -Separator " ")
    if ($WhatIfPreference) {
        Write-Host "What if: $display"
        return
    }
    Write-Host "> $display"
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "Command failed with exit code ${LASTEXITCODE}: $display"
    }
}

if (!(Test-Path -LiteralPath $TargetsPath)) {
    throw "Deployment target file not found: $TargetsPath. Copy deploy.targets.example.json to deploy.targets.json and enter each TV's Developer Mode details."
}

$targetsConfig = Get-Content -LiteralPath $TargetsPath -Raw | ConvertFrom-Json
if (!$targetsConfig.targets -or $targetsConfig.targets.Count -eq 0) {
    throw "At least one deployment target is required."
}

$sdb = Find-TizenTool "sdb"
$tizen = Find-TizenTool "tizen"

if (!$SkipBuild) {
    if (!$targetsConfig.certificateProfile) {
        throw "certificateProfile is required to build a signed receiver package."
    }
    Invoke-TizenCommand $tizen @("build-web", "--", $appDirectory)
    $buildDirectory = Join-Path $appDirectory ".build"
    Invoke-TizenCommand $tizen @("package", "-t", "wgt", "-s", [string]$targetsConfig.certificateProfile, "-o", $appDirectory, "--", $buildDirectory)
}

$package = Get-ChildItem -LiteralPath $appDirectory -Filter "*.wgt" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (!$package) {
    throw "No WGT package was found in $appDirectory. Build the receiver first or omit -SkipBuild."
}

foreach ($target in $targetsConfig.targets) {
    $name = [string]$target.name
    $host = [string]$target.host
    $serial = [string]$target.serial
    if (!$name -or !$host -or !$serial) {
        throw "Each target needs name, host, and serial values."
    }

    Write-Host "`nDeploying MultiHub Receiver to $name ($serial)"
    Invoke-TizenCommand $sdb @("connect", $host)
    # Uninstall is deliberately tolerant: first-time installs report a non-zero exit code here.
    Invoke-TizenCommand $sdb @("-s", $serial, "uninstall", $packageId) -AllowFailure
    Invoke-TizenCommand $tizen @("install", "-n", $package.FullName, "-s", $serial)
    Invoke-TizenCommand $tizen @("run", "-p", $packageId, "-s", $serial)
    Write-Host "Opened $appId on $name."
}
