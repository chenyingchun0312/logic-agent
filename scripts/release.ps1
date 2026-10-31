#Requires -Version 7.2
<#
.SYNOPSIS
  Assemble, verify and zip the portable Windows bundle.

.DESCRIPTION
  Inputs come only from this checkout and .deps/ (scripts/bootstrap.ps1) plus
  dslab/build (scripts/build.ps1). Nothing is reused from an older bundle.

  Gates, in order: checksum verify, bundled-Python smoke test, pytest against the
  bundle, CHECK -SkipHardware, cache cleanup + re-verify, zip, fresh-extract verify.
  The hardware gate (CHECK.cmd / SELF-TEST.cmd with a device) stays manual.

.EXAMPLE
  pwsh scripts/release.ps1
  pwsh scripts/release.ps1 -DriverDir 'C:\Program Files\DSView\Drivers'
#>
param(
    [string]$Version = '',
    [string]$OutDir = '',
    # DreamSourceLab WinUSB driver folder (contains dpinst-amd64.exe). Optional.
    [string]$DriverDir = '',
    # Optional Zadig executable to ship as a driver fallback.
    [string]$Zadig = '',
    [string]$BundleName = 'logic-agent',
    [switch]$NoDriver,
    [switch]$SkipTests,
    [switch]$NoZip
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$deps = Read-Deps
$p = Get-DepPaths
if (-not $Version) { $Version = Get-ProjectVersion }
if (-not $OutDir) { $OutDir = Join-Path $root 'dist' }
$OutDir = [IO.Path]::GetFullPath($OutDir)
$buildDate = (Get-Date).ToString('yyyy-MM-dd')

# ---------------------------------------------------------------- preflight
$required = [ordered]@{
    'dslab.exe (scripts/build.ps1)'           = $p.DslabExe
    'DSView res (scripts/bootstrap.ps1)'      = (Join-Path $p.DsviewRes 'DSLogicPlus.bin')
    'firmware (scripts/bootstrap.ps1)'        = $p.Firmware
    'firmware license (scripts/bootstrap.ps1)' = $p.FirmwareLic
    'sigrok-cli (scripts/bootstrap.ps1)'      = (Join-Path $p.SigrokBin 'sigrok-cli.exe')
    'decoders (scripts/bootstrap.ps1)'        = (Join-Path $p.Decoders 'i2c\pd.py')
    'embeddable Python (scripts/bootstrap.ps1)' = (Join-Path $p.PythonEmbed 'python.exe')
}
foreach ($k in $required.Keys) { if (-not (Test-Path -LiteralPath $required[$k])) { throw "missing $k`: $($required[$k])" } }
if ((Get-Sha256 $p.Firmware) -ne $deps.firmware.sha256) { throw 'firmware hash does not match deps.json' }
if (-not $NoDriver) {
    if (-not $DriverDir) {
        $DriverDir = @($env:DSVIEW_DRIVER_DIR, 'C:\Program Files\DSView\Drivers') |
            Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'dpinst-amd64.exe')) } | Select-Object -First 1
    }
    if (-not $DriverDir) { Write-Warning 'WinUSB driver not found (install DSView or pass -DriverDir); bundle will rely on Zadig/DSView for drivers' }
    if (-not $Zadig -and $env:ZADIG_EXE) { $Zadig = $env:ZADIG_EXE }
}
Sync-DecoderOverlay
$git = (& git -C $root rev-parse --short HEAD 2>$null)
$dirty = (& git -C $root status --porcelain 2>$null)
if ($dirty) { Write-Warning 'working tree has uncommitted changes; the bundle will not match any commit' ; $git = "$git-dirty" }

# ---------------------------------------------------------------- stage
$stageRoot = Join-Path $OutDir 'stage'
$bundle = Join-Path $stageRoot $BundleName
$baseName = "$BundleName-dslogic-plus-v$Version-win-x64"
if (Test-Path -LiteralPath $OutDir) { Remove-Item -LiteralPath $OutDir -Recurse -Force }
$runtime = Join-Path $bundle 'runtime'
$bin = Join-Path $runtime 'bin'
$res = Join-Path $bin 'res'
$licenses = Join-Path $runtime 'licenses'
New-Item -ItemType Directory -Force -Path $res, $licenses, (Join-Path $runtime 'scripts'), (Join-Path $bundle 'firmware'),
    (Join-Path $bundle 'scripts'), (Join-Path $bundle 'docs'), (Join-Path $bundle 'data') | Out-Null

function Copy-Tree([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Get-ChildItem -LiteralPath $From -Force | Copy-Item -Destination $To -Recurse -Force
}

Write-Step "stage $bundle"
# dslab + its DLLs, sigrok-cli runtime, DSView resources (FPGA bitstreams under MIT license.txt)
Copy-Item -LiteralPath $p.DslabExe -Destination $bin
Get-ChildItem -LiteralPath $p.DslabBuild -Filter '*.dll' | Copy-Item -Destination $bin
Copy-Tree $p.SigrokBin $bin
Get-ChildItem -LiteralPath $p.DsviewRes -File | Where-Object { $_.Extension -in '.bin', '.dsc', '.fw', '.txt' } |
    Copy-Item -Destination $res
Copy-Item -LiteralPath $p.Firmware -Destination $res
Copy-Tree $p.Decoders (Join-Path $runtime 'decoders')

# Embeddable CPython; the ._pth file replaces PYTHONPATH (which embed mode ignores).
$py = Join-Path $runtime 'python'
Copy-Tree $p.PythonEmbed $py
$pth = Get-ChildItem -LiteralPath $py -Filter 'python3*._pth' | Select-Object -First 1
if (-not $pth) { throw 'embeddable Python has no ._pth file' }
$zipName = ($pth.BaseName) + '.zip'
[IO.File]::WriteAllLines($pth.FullName, @($zipName, '.', '..\app'), [Text.Encoding]::ASCII)

Copy-Tree (Join-Path $root 'logic_agent') (Join-Path $runtime 'app\logic_agent')
Copy-Item -LiteralPath (Join-Path $root 'scripts\deploy-check.ps1') -Destination (Join-Path $runtime 'scripts')
Get-ChildItem -LiteralPath (Join-Path $root 'packaging\runtime') -File | Copy-Item -Destination $runtime
Get-ChildItem -LiteralPath (Join-Path $root 'packaging\launchers') -File | Copy-Item -Destination $bundle
Copy-Tree (Join-Path $root 'packaging\portable-scripts') (Join-Path $bundle 'scripts')

# Docs: bundle-specific docs plus the shared ones under their bundle names.
$docs = Join-Path $bundle 'docs'
Get-ChildItem -LiteralPath (Join-Path $root 'packaging\docs') -File | Copy-Item -Destination $docs
Copy-Item -LiteralPath (Join-Path $root 'docs\protocol-decoding.md') -Destination (Join-Path $docs 'PROTOCOLS.md')
Copy-Item -LiteralPath (Join-Path $root 'docs\upstream-sync.md') -Destination (Join-Path $docs 'UPSTREAM-SYNC.md')
Copy-Item -LiteralPath (Join-Path $root 'docs\dslogic-plus.md') -Destination (Join-Path $docs 'DSLOGIC-PLUS.md')
Copy-Item -LiteralPath (Join-Path $root 'packaging\README.portable.md') -Destination (Join-Path $bundle 'README.md')
Copy-Item -LiteralPath (Join-Path $root 'THIRD_PARTY.md') -Destination (Join-Path $bundle 'THIRD-PARTY-NOTICES.md')

# Firmware and its license travel together.
Copy-Item -LiteralPath $p.Firmware -Destination (Join-Path $bundle 'firmware')
Copy-Item -LiteralPath $p.FirmwareLic -Destination (Join-Path $bundle 'firmware\LICENSE-DreamSourceLab.txt')

# Licenses and the corresponding-source pointer for the GPL parts.
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $licenses 'logic-agent-MIT.txt')
Copy-Item -LiteralPath (Join-Path $root 'dslab\COPYING') -Destination (Join-Path $licenses 'dslab-GPL-3.0.txt')
Copy-Item -LiteralPath (Join-Path $p.DsviewRes 'license.txt') -Destination (Join-Path $licenses 'DreamSourceLab-res-MIT.txt')
$sigrokCopying = Join-Path (Split-Path -Parent $p.SigrokBin) 'COPYING'
if (Test-Path -LiteralPath $sigrokCopying) { Copy-Item -LiteralPath $sigrokCopying -Destination (Join-Path $licenses 'sigrok-cli-GPL-3.0.txt') }
$pyLicense = Join-Path $p.PythonEmbed 'LICENSE.txt'
if (Test-Path -LiteralPath $pyLicense) { Copy-Item -LiteralPath $pyLicense -Destination (Join-Path $licenses 'python-PSF.txt') }
Copy-Item -LiteralPath (Join-Path $root 'decoders_ext\smart_qspi\PORTING.md') -Destination (Join-Path $licenses 'smart_qspi-PORTING.md')
Copy-Tree (Join-Path $root 'patches') (Join-Path $licenses 'source\patches')

if (-not $NoDriver) {
    $driverOut = Join-Path $bundle 'driver'
    if ($DriverDir) { Copy-Tree $DriverDir (Join-Path $driverOut 'DSView-Drivers') }
    if ($Zadig -and (Test-Path -LiteralPath $Zadig)) {
        New-Item -ItemType Directory -Force -Path $driverOut | Out-Null
        Copy-Item -LiteralPath $Zadig -Destination (Join-Path $driverOut 'zadig.exe')
    }
}
Set-Content -LiteralPath (Join-Path $bundle 'data\README.txt') -Encoding utf8 'Output directory. Safe to delete; recreated automatically.'

# ---------------------------------------------------------------- metadata
$bundlePy = Join-Path $py 'python.exe'
$protocolCount = (& $bundlePy -B -c 'from logic_agent.decoders import supported_protocols; print(len(supported_protocols()))')
if ($LASTEXITCODE -ne 0 -or $protocolCount -notmatch '^\d+$') { throw "bundled Python cannot import logic_agent: $protocolCount" }
$sigrokVersion = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $p.SigrokBin) 'VERSION.txt') -ErrorAction SilentlyContinue | Select-Object -First 1
$versionText = @"
logic-agent portable bundle for DSLogic Plus
============================================

Bundle version : $Version
Build date     : $buildDate
Source commit  : $git
Target device  : DSLogic Plus (USB 2A0E:0020)
DSView         : $($deps.dsview.tag) / $($deps.dsview.commit) + $($deps.dsview.patch)
sigrok-cli     : $sigrokVersion
Python         : $($deps.python_embed.version) (embeddable)
Protocols      : $protocolCount public entries
Firmware       : firmware/DSLogicPlus.fw ($($deps.firmware.bytes) bytes)
Firmware SHA256: $($deps.firmware.sha256)
Driver bundled : $(if ($DriverDir -and -not $NoDriver) { 'yes' } else { 'no' })
Verification   : checksums.sha256 (all packaged files)
Notes          : Normal devices do not need a firmware write.
                 Firmware recovery is only for USB 2A0E:0001 bootloader.
"@
Set-Content -LiteralPath (Join-Path $bundle 'VERSION.txt') -Value $versionText -Encoding utf8

function Remove-Caches([string]$Dir) {
    foreach ($rel in 'runtime\runs', 'runs', 'data\selftest', 'data\captures', 'data\capture-smoke', '.logic_agent_data') {
        $t = Join-Path $Dir $rel
        if (Test-Path -LiteralPath $t) { Remove-Item -LiteralPath $t -Recurse -Force }
    }
    Get-ChildItem -LiteralPath $Dir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $Dir -Recurse -File -Filter '*.pyc' -ErrorAction SilentlyContinue | Remove-Item -Force
}
function Write-Checksums([string]$Dir) {
    $files = Get-ChildItem -LiteralPath $Dir -Recurse -File | Where-Object { $_.Name -ne 'checksums.sha256' } | Sort-Object FullName
    $lines = foreach ($f in $files) { '{0}  {1}' -f (Get-Sha256 $f.FullName), $f.FullName.Substring($Dir.Length + 1).Replace('\', '/') }
    [IO.File]::WriteAllLines((Join-Path $Dir 'checksums.sha256'), [string[]]$lines, [Text.Encoding]::ASCII)
    @($files).Count
}
function Test-Checksums([string]$Dir) {
    $out = & (Join-Path $Dir 'runtime\python\python.exe') -B (Join-Path $Dir 'scripts\verify-files.py') $Dir
    if ($LASTEXITCODE -ne 0) { throw "checksum verification failed in ${Dir}: $out" }
    $out | Select-Object -Last 1
}

Remove-Caches $bundle
$null = Write-Checksums $bundle
Write-Step "verify: $(Test-Checksums $bundle)"

# ---------------------------------------------------------------- gates
if (-not $SkipTests) {
    Write-Step 'smoke: bundled launcher'
    $caps = & (Join-Path $runtime 'logic-agent.cmd') capabilities 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "logic-agent.cmd capabilities failed: $caps" }

    Write-Step 'pytest against the bundle runtime'
    $testPy = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $testPy)) { $testPy = 'python' }
    $saved = @{}
    $envs = @{
        LOGIC_AGENT_DECODER_EXE = Join-Path $bin 'sigrok-cli.exe'
        SIGROKDECODE_DIR        = Join-Path $runtime 'decoders'
        DSLAB_EXE               = Join-Path $bin 'dslab.exe'
        DSLAB_RES_DIR           = $res
        PYTHONDONTWRITEBYTECODE = '1'
    }
    foreach ($k in $envs.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $envs[$k]) }
    Push-Location $root
    try {
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'build') | Out-Null
        $log = & $testPy -m pytest -q -rs -p no:cacheprovider --basetemp (Join-Path $root 'build\pytest-release') 2>&1
        $code = $LASTEXITCODE
        $log | Select-Object -Last 5 | Out-Host
        if ($code -ne 0) { throw 'pytest failed against the bundle' }
        if ($log -match 'SKIPPED .*decoder runtime') { throw 'decoder tests skipped against the bundle; the release gate requires them' }
    } finally {
        Pop-Location
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }

    # Same entry point colleagues use, under Windows PowerShell 5.1.
    Write-Step 'CHECK -SkipHardware (Windows PowerShell)'
    $check = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bundle 'scripts\check.ps1') -Root $bundle -SkipHardware 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "CHECK -SkipHardware failed:`n$check" }
}

Remove-Caches $bundle
$fileCount = Write-Checksums $bundle
Write-Step "re-verify: $(Test-Checksums $bundle)"

# ---------------------------------------------------------------- archive
if (-not $NoZip) {
    $zip = Join-Path $OutDir "$baseName.zip"
    Write-Step "zip $zip"
    Compress-Archive -Path $bundle -DestinationPath $zip -CompressionLevel Optimal
    $zipHash = Get-Sha256 $zip
    "$zipHash  $([IO.Path]::GetFileName($zip))" | Set-Content -LiteralPath "$zip.sha256" -Encoding ascii -NoNewline
    [pscustomobject]@{
        version         = $Version
        built_at        = (Get-Date).ToString('o')
        source_commit   = $git
        archive         = [IO.Path]::GetFileName($zip)
        bytes           = (Get-Item -LiteralPath $zip).Length
        sha256          = $zipHash
        files           = $fileCount + 1
        firmware_sha256 = $deps.firmware.sha256
        dsview          = "$($deps.dsview.tag) / $($deps.dsview.commit)"
    } | ConvertTo-Json | Set-Content -LiteralPath "$zip.json" -Encoding utf8

    # Never trust the staging dir alone: verify what a colleague will actually unzip.
    $fresh = Join-Path $OutDir 'verify'
    Expand-Archive -LiteralPath $zip -DestinationPath $fresh
    Write-Step "fresh extract: $(Test-Checksums (Join-Path $fresh $BundleName))"
    Remove-Item -LiteralPath $fresh -Recurse -Force
    Write-Host "ARCHIVE=$zip"
    Write-Host "SHA256=$zipHash"
}
Write-Host "BUNDLE=$bundle"
Write-Host "FILES=$($fileCount + 1)"
Write-Host ''
Write-Host 'Hardware gate (manual, serial, device connected, DSView closed):' -ForegroundColor Yellow
Write-Host "  & '$bundle\CHECK.cmd'"
Write-Host "  & '$bundle\SELF-TEST.cmd'"
Write-Host 'Both must report top-level "ok": true before publishing.'
