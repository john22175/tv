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
    $argumentDisplay = ($Arguments | ForEach-Object { "`"$_`"" }) -join " "
    $display = "`"$Tool`" $argumentDisplay"
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

function Confirm-SdbTarget([string]$SdbTool, [string]$Serial) {
    if ($WhatIfPreference) {
        Write-Host "What if: verify SDB target $Serial"
        return
    }
    # sdb connect can report a network error but still return exit code 0.
    # Verify the exact serial is visible before uninstalling anything.
    $devices = (& $SdbTool devices | Out-String)
    $serialPattern = "(?m)^" + [Regex]::Escape($Serial) + "\s+device\b"
    if ($devices -notmatch $serialPattern) {
        throw "SDB target '$Serial' is not connected in Developer Mode."
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

$deployPackage = Join-Path $appDirectory "mhub_receiver_deploy.wgt"

if (!$SkipBuild) {
    if (!$targetsConfig.certificateProfile) {
        throw "certificateProfile is required to build a signed receiver package."
    }
    $buildStartedAt = (Get-Date).AddSeconds(-2)
    Invoke-TizenCommand $tizen @("build-web", "--", $appDirectory)
    $buildDirectory = @(".buildResult", ".build") |
        ForEach-Object { Join-Path $appDirectory $_ } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if (!$buildDirectory) {
        throw "Tizen did not create a build output directory."
    }
    Invoke-TizenCommand $tizen @("package", "-t", "wgt", "-s", [string]$targetsConfig.certificateProfile, "-o", $appDirectory, "--", $buildDirectory)
    $generatedPackage = Get-ChildItem -LiteralPath $appDirectory -Filter "*.wgt" -File |
        Where-Object { $_.LastWriteTime -ge $buildStartedAt -and $_.FullName -ne $deployPackage } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (!$generatedPackage) {
        throw "Tizen did not produce a WGT package."
    }
    # The Samsung installer accepts this package when its filename is simple;
    # the generated display-name filename contains spaces and is rejected.
    Copy-Item -LiteralPath $generatedPackage.FullName -Destination $deployPackage -Force
}

if (!(Test-Path -LiteralPath $deployPackage)) {
    if ($WhatIfPreference) {
        Write-Host "What if: use deployment package $deployPackage"
    } else {
        throw "Deployment package not found: $deployPackage. Build the receiver before using -SkipBuild."
    }
}

$failures = @()
foreach ($target in $targetsConfig.targets) {
    $name = [string]$target.name
    $targetHost = [string]$target.host
    $serial = [string]$target.serial
    if (!$name -or !$targetHost -or !$serial) {
        throw "Each target needs name, host, and serial values."
    }

    try {
        Write-Host "`nDeploying MultiHub Receiver to $name ($serial)"
        Invoke-TizenCommand $sdb @("connect", $targetHost)
        Confirm-SdbTarget $sdb $serial
        # Uninstall is deliberately tolerant: first-time installs report a non-zero exit code here.
        Invoke-TizenCommand $sdb @("-s", $serial, "uninstall", $packageId) -AllowFailure
        Invoke-TizenCommand $tizen @("install", "-n", $deployPackage, "-s", $serial)
        # tizen run is unreliable with this CLI version; Samsung's app launcher
        # opens the exact app ID directly on the selected TV.
        Invoke-TizenCommand $sdb @("-s", $serial, "shell", "0", "app_launcher", "-s", $appId)
        Write-Host "Opened $appId on $name."
    } catch {
        $failures += "$name ($serial): $($_.Exception.Message)"
        Write-Warning "Deployment failed for ${name}: $($_.Exception.Message)"
    }
}

if ($failures.Count) {
    throw ("Receiver deployment failed for: " + ($failures -join "; "))
}
