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
    # Keep native-tool logs visible but off PowerShell's success stream. A
    # package-building helper returns its WGT path, and unconsumed tool output
    # would otherwise be captured as part of that path.
    & $Tool @Arguments | Out-Host
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

$deployRoot = Join-Path (Split-Path -Parent $appDirectory) ".deploy"
$deployRunDirectory = Join-Path $deployRoot ("run-" + [guid]::NewGuid().ToString("N"))

foreach ($target in $targetsConfig.targets) {
    $name = [string]$target.name
    $targetHost = [string]$target.host
    $serial = [string]$target.serial
    $receiverId = [string]$target.receiverId
    if (!$name -or !$targetHost -or !$serial -or $receiverId -notmatch "^tv-[1-6]$") {
        throw "Each target needs name, host, serial, and a receiverId from tv-1 through tv-6."
    }
}

$receiverIds = @($targetsConfig.targets | ForEach-Object { [string]$_.receiverId })
if (($receiverIds | Select-Object -Unique).Count -ne $receiverIds.Count) {
    throw "Every deployment target must have a unique receiverId."
}

if (!$targetsConfig.certificateProfile) {
    throw "certificateProfile is required to build a signed receiver package."
}

if (!$SkipBuild) {
    Invoke-TizenCommand $tizen @("build-web", "--", $appDirectory)
}

$buildDirectory = @(".buildResult", ".build") |
    ForEach-Object { Join-Path $appDirectory $_ } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (!$buildDirectory -and -not $WhatIfPreference) {
    throw "Tizen did not create a build output directory."
}

function New-ReceiverPackage([string]$ReceiverId) {
    $targetDirectory = Join-Path $deployRunDirectory $ReceiverId
    $targetBuildDirectory = Join-Path $targetDirectory "app"
    $packageDirectory = Join-Path $targetDirectory "package"
    $deployPackage = Join-Path $targetDirectory ("mhub_receiver_" + $ReceiverId + ".wgt")
    if ($WhatIfPreference) {
        Write-Host "What if: create receiver-specific package $deployPackage"
        return $deployPackage
    }

    New-Item -ItemType Directory -Path $targetBuildDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $packageDirectory -Force | Out-Null
    Copy-Item -Path (Join-Path $buildDirectory "*") -Destination $targetBuildDirectory -Recurse -Force

    # A per-TV package identity means a factory-reset/uninstalled app never
    # needs the user to pair it with a remote before dashboard staging works.
    $targetScript = Join-Path $targetBuildDirectory "js\receiver-target.js"
    $targetContents = @"
(function attachReceiverTarget(global) {
  "use strict";
  global.MultiHubReceiverTarget = { id: "$ReceiverId" };
}(globalThis));
"@
    [System.IO.File]::WriteAllText($targetScript, $targetContents, (New-Object System.Text.UTF8Encoding($false)))

    $packageStartedAt = (Get-Date).AddSeconds(-2)
    Invoke-TizenCommand $tizen @("package", "-t", "wgt", "-s", [string]$targetsConfig.certificateProfile, "-o", $packageDirectory, "--", $targetBuildDirectory)
    $generatedPackage = Get-ChildItem -LiteralPath $packageDirectory -Filter "*.wgt" -File |
        Where-Object { $_.LastWriteTime -ge $packageStartedAt } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (!$generatedPackage) {
        throw "Tizen did not produce a WGT package for $ReceiverId."
    }
    # The Samsung installer rejects generated filenames containing spaces.
    Copy-Item -LiteralPath $generatedPackage.FullName -Destination $deployPackage -Force
    return $deployPackage
}

$failures = @()
foreach ($target in $targetsConfig.targets) {
    $name = [string]$target.name
    $targetHost = [string]$target.host
    $serial = [string]$target.serial
    $receiverId = [string]$target.receiverId

    try {
        Write-Host "`nDeploying MultiHub Receiver to $name ($serial, $receiverId)"
        $deployPackage = New-ReceiverPackage $receiverId
        Invoke-TizenCommand $sdb @("connect", $targetHost)
        Confirm-SdbTarget $sdb $serial
        # Uninstall is deliberately tolerant: first-time installs report a non-zero exit code here.
        Invoke-TizenCommand $sdb @("-s", $serial, "uninstall", $packageId) -AllowFailure
        Invoke-TizenCommand $tizen @("install", "-n", $deployPackage, "-s", $serial)
        # `tizen run` and `app_launcher` are unreliable on these TVs. Samsung's
        # documented SDB command launches the exact installed application ID.
        Invoke-TizenCommand $sdb @("-s", $serial, "shell", "0", "execute", $appId)
        if ($WhatIfPreference) {
            Write-Host "What if: open $appId on $name."
        } else {
            Write-Host "Opened $appId on $name."
        }
    } catch {
        $failures += "$name ($serial): $($_.Exception.Message)"
        Write-Warning "Deployment failed for ${name}: $($_.Exception.Message)"
    }
}

if ($failures.Count) {
    throw ("Receiver deployment failed for: " + ($failures -join "; "))
}
