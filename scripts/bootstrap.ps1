<#
.SYNOPSIS
  Fetch every third-party input into .deps/ so the repo can be built, tested and released.

.DESCRIPTION
  Idempotent: finished steps are skipped. Everything lands in .deps/ (and downloads in
  .cache/), both git-ignored. Versions and hashes come from deps.json.

  .deps/dsview         DSView source at the pinned commit with patches/ applied
  .deps/firmware       DSLogicPlus.fw + DreamSourceLab res license
  .deps/sigrok         minimal sigrok-cli runtime (bin/) and decoders (+ decoders_ext overlay)
  .deps/python-embed   embeddable CPython for the portable bundle
  .deps/msys64         MSYS2 UCRT64 toolchain, only when no existing MSYS2 is found
  .venv                developer virtualenv with pytest

.EXAMPLE
  pwsh scripts/bootstrap.ps1
  pwsh scripts/bootstrap.ps1 -DsviewRepo https://gitlab.example.com/mirror/DSView.git
#>
#Requires -Version 7.2
param(
    # Git URL or local path of a DSView mirror. Defaults to $env:DSVIEW_GIT_URL, then deps.json.
    [string]$DsviewRepo = '',
    # Existing MSYS2 root (folder containing usr\bin\bash.exe).
    [string]$Msys2Root = '',
    # Existing sigrok-cli install (folder containing sigrok-cli.exe and share\libsigrokdecode).
    [string]$SigrokRoot = '',
    [string]$Python = 'python',
    [switch]$SkipMsys2,
    [switch]$SkipSigrok,
    [switch]$SkipPythonEmbed,
    [switch]$SkipVenv,
    [switch]$Force
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$deps = Read-Deps
$paths = Get-DepPaths
New-Item -ItemType Directory -Force -Path (Join-Path $root '.deps') | Out-Null
$summary = [ordered]@{}

# ---------------------------------------------------------------- DSView source
function Test-DsviewReady {
    if (-not (Test-Path -LiteralPath (Join-Path $paths.Dsview '.git'))) { return $false }
    $head = (& git -C $paths.Dsview rev-parse HEAD 2>$null)
    if ($head -ne $deps.dsview.commit) { return $false }
    & git -C $paths.Dsview apply --reverse --check (Join-Path $root $deps.dsview.patch) 2>$null
    $LASTEXITCODE -eq 0
}
if ($Force -or -not (Test-DsviewReady)) {
    $url = if ($DsviewRepo) { $DsviewRepo } elseif ($env:DSVIEW_GIT_URL) { $env:DSVIEW_GIT_URL } else { $deps.dsview.repo }
    Write-Step "DSView $($deps.dsview.tag) from $url"
    if (Test-Path -LiteralPath $paths.Dsview) { Remove-Item -LiteralPath $paths.Dsview -Recurse -Force }
    & git init --quiet $paths.Dsview
    & git -C $paths.Dsview remote add origin $url
    & git -C $paths.Dsview fetch --quiet --depth 1 origin $deps.dsview.commit
    if ($LASTEXITCODE -ne 0) {
        # Some mirrors refuse fetch-by-SHA; the tag points at the same commit.
        & git -C $paths.Dsview fetch --quiet --depth 1 origin "refs/tags/$($deps.dsview.tag):refs/tags/$($deps.dsview.tag)"
        if ($LASTEXITCODE -ne 0) { throw "cannot fetch DSView from $url" }
    }
    & git -C $paths.Dsview -c advice.detachedHead=false checkout --quiet $deps.dsview.commit
    if ($LASTEXITCODE -ne 0) { throw "DSView commit $($deps.dsview.commit) not available from $url" }
    & git -C $paths.Dsview apply --whitespace=nowarn (Join-Path $root $deps.dsview.patch)
    if ($LASTEXITCODE -ne 0) { throw "patch does not apply: $($deps.dsview.patch)" }
}
$summary.dsview = "$($paths.Dsview) @ $($deps.dsview.commit.Substring(0, 7)) + patch"

# ---------------------------------------------------------------- firmware
$fwDir = Split-Path -Parent $paths.Firmware
New-Item -ItemType Directory -Force -Path $fwDir | Out-Null
function Install-Pinned([string]$Target, [string]$Sha, [string]$Url, [string[]]$LocalCandidates) {
    if ((Test-Path -LiteralPath $Target) -and (Get-Sha256 $Target) -eq $Sha) { return }
    foreach ($c in $LocalCandidates) {
        if ($c -and (Test-Path -LiteralPath $c) -and (Get-Sha256 $c) -eq $Sha) {
            Copy-Item -LiteralPath $c -Destination $Target -Force
            return
        }
    }
    $file = Get-PinnedFile $Url $Sha ("{0}-{1}" -f $Sha.Substring(0, 12), (Split-Path -Leaf $Target))
    Copy-Item -LiteralPath $file -Destination $Target -Force
}
Install-Pinned $paths.Firmware $deps.firmware.sha256 $deps.firmware.url @(
    (Join-Path $root '.local\firmware\DSLogicPlus.fw'), 'C:\Program Files\DSView\res\DSLogicPlus.fw')
Install-Pinned $paths.FirmwareLic $deps.firmware.license_sha256 $deps.firmware.license_url @(
    (Join-Path $paths.DsviewRes 'license.txt'))
$summary.firmware = "$($paths.Firmware) ($($deps.firmware.sha256.Substring(0, 12)))"

# ---------------------------------------------------------------- MSYS2 UCRT64
if (-not $SkipMsys2) {
    $msys = Find-Msys2 $Msys2Root
    if (-not $msys) {
        Write-Step 'MSYS2 not found; installing into .deps\msys64'
        $sfxUrl = $deps.msys2.installer_url
        $expected = ((& curl.exe -fsSL "$sfxUrl.sha256") -split '\s+')[0].ToLowerInvariant()
        if ($LASTEXITCODE -ne 0 -or $expected -notmatch '^[0-9a-f]{64}$') { throw "cannot read $sfxUrl.sha256" }
        $sfx = Get-PinnedFile $sfxUrl $expected 'msys2-base-x86_64-latest.sfx.exe'
        & $sfx -y "-o$(Join-Path $root '.deps')" | Out-Null
        $msys = Join-Path $root '.deps\msys64'
        if (-not (Test-Path -LiteralPath (Join-Path $msys 'usr\bin\bash.exe'))) { throw 'MSYS2 extraction failed' }
    }
    if ($Force -or -not (Test-Msys2Ready $msys)) {
        Write-Step "pacman install into $msys"
        $env:MSYSTEM = 'UCRT64'; $env:CHERE_INVOKING = '1'
        $bash = Join-Path $msys 'usr\bin\bash.exe'
        & $bash -lc 'true' | Out-Null   # first login initialises keyring and /etc
        & $bash -lc ("pacman -Sy --needed --noconfirm " + ($deps.msys2.packages -join ' '))
        if ($LASTEXITCODE -ne 0 -or -not (Test-Msys2Ready $msys)) { throw "MSYS2 package install failed in $msys" }
    }
    $summary.msys2 = $msys
}

# ---------------------------------------------------------------- sigrok-cli + decoders
if (-not $SkipSigrok) {
    $sigrokDest = Split-Path -Parent $paths.SigrokBin
    if ($Force -or -not (Test-Path -LiteralPath (Join-Path $paths.SigrokBin 'sigrok-cli.exe'))) {
        $src = Find-SigrokRoot $SigrokRoot
        if ($src) {
            Write-Step "sigrok-cli from $src"
            Copy-SigrokRuntime $src $sigrokDest
        } elseif ($deps.sigrok_cli.url) {
            $zip = Get-PinnedFile $deps.sigrok_cli.url $deps.sigrok_cli.sha256 "sigrok-cli-$($deps.sigrok_cli.version)-win-x64.zip"
            if (Test-Path -LiteralPath $sigrokDest) { Remove-Item -LiteralPath $sigrokDest -Recurse -Force }
            Expand-Archive -LiteralPath $zip -DestinationPath $sigrokDest
        } else {
            throw @"
sigrok-cli not found. Do one of:
  - install it from https://sigrok.org/wiki/Downloads (Windows, sigrok-cli) and pass -SigrokRoot <dir>
  - set SIGROK_CLI_ROOT, or put it under %ZEPHYR_ENV_HOME%\sigrok-cli
  - set sigrok_cli.url/sha256 in deps.json to a repackaged archive (scripts/pack-sigrok-cli.ps1)
  - or rerun with -SkipSigrok (protocol decoder tests will be skipped, no release possible)
"@
        }
    }
    Sync-DecoderOverlay
    $summary.sigrok = (Get-Content -LiteralPath (Join-Path $sigrokDest 'VERSION.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
}

# ---------------------------------------------------------------- embeddable Python for the bundle
if (-not $SkipPythonEmbed) {
    if ($Force -or -not (Test-Path -LiteralPath (Join-Path $paths.PythonEmbed 'python.exe'))) {
        $pe = $deps.python_embed
        $zip = Get-PinnedFile $pe.url $pe.sha256 (Split-Path -Leaf $pe.url)
        if (Test-Path -LiteralPath $paths.PythonEmbed) { Remove-Item -LiteralPath $paths.PythonEmbed -Recurse -Force }
        Expand-Archive -LiteralPath $zip -DestinationPath $paths.PythonEmbed
    }
    $summary.python_embed = "$($deps.python_embed.version) in $($paths.PythonEmbed)"
}

# ---------------------------------------------------------------- developer venv
if (-not $SkipVenv) {
    $venvPy = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPy)) {
        & $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -ne 0) { throw "Python >= 3.11 required; pass -Python <path>" }
        Write-Step 'create .venv'
        & $Python -m venv (Join-Path $root '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
    }
    & $venvPy -c "import pytest, logic_agent" 2>$null
    if ($Force -or $LASTEXITCODE -ne 0) {
        Write-Step 'pip install -e .[dev]'
        & $venvPy -m pip install --quiet --disable-pip-version-check -e "$root[dev]"
        if ($LASTEXITCODE -ne 0) { throw 'pip install failed (set PIP_INDEX_URL for a mirror)' }
    }
    $summary.venv = $venvPy
}

Write-Host ''
$summary.GetEnumerator() | ForEach-Object { '{0,-13} {1}' -f $_.Key, $_.Value }
Write-Host ''
Write-Host 'Next: pwsh scripts/build.ps1; pwsh scripts/test.ps1' -ForegroundColor Green
