#Requires -Version 7.2
<#
.SYNOPSIS
  Mint the pinned sigrok-cli runtime zip from the upstream sigrok.org installer.

.DESCRIPTION
  sigrok.org publishes the Windows sigrok-cli build only as an NSIS installer
  (no zip, no GitHub binary release), and its version string is constant across
  nightly rebuilds, so the upstream URL cannot be the pin. The repo therefore
  pins a zip we mint ourselves: scripts/bootstrap.ps1 downloads deps.json's
  sigrok_cli.url, verifies sha256 and unpacks it into .deps/sigrok.

  This script downloads the installer from a pinned https URL (never from a local
  file or an already extracted tree), unpacks it with 7-Zip, normalises it exactly
  like bootstrap does (bin/ runtime + decoders/ tree + COPYING + VERSION.txt) and
  writes sigrok-cli-<version><stamp>-win64.zip.

  Run it from the 'sigrok runtime' workflow: download, unpack, repack, upload and
  -UpdateDeps all happen on the runner, so no machine needs sigrok-cli, 7-Zip or
  a prepared extraction.

.PARAMETER FromInstaller
  https URL of the upstream Windows installer (.exe). Defaults to
  deps.json sigrok_cli.upstream.url.

.PARAMETER Stamp
  Suffix appended to the asset name, e.g. '-20261103'. Re-mints of the same
  nightly build need it, otherwise the asset name repeats.

.EXAMPLE
  pwsh scripts/pack-sigrok-cli.ps1 -Tag sigrok-runtime-20261103 -Stamp -20261103 -UpdateDeps
#>
param(
    [string]$FromInstaller = '',
    [string]$OutDir = '',
    [string]$Version = '',
    [string]$Stamp = '',
    [string]$Tag = '',
    [string]$Url = '',
    [string]$InstallerSha256 = '',
    [switch]$SkipVersionProbe,
    [switch]$UpdateDeps,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')

$root = Get-RepoRoot
$deps = Read-Deps
if (-not $OutDir) { $OutDir = Join-Path $root 'dist\sigrok' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = [IO.Path]::GetFullPath($OutDir)

$work = Join-Path ([IO.Path]::GetTempPath()) ('sigrok-pack-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Force -Path $work | Out-Null

    # ------------------------------------------------------------ sigrok source
    # URL only: the installer is fetched, unpacked and repacked here, on the runner.
    # Nothing is ever read from a developer machine (no local sigrok-cli, no local
    # extraction, no local 7-Zip copy of the tree).
    $upstream = if ($deps.sigrok_cli.PSObject.Properties['upstream']) { $deps.sigrok_cli.upstream } else { $null }
    $installer = $FromInstaller
    if (-not $installer -and $upstream) { $installer = $upstream.url }
    if (-not $installer) {
        throw @'
no sigrok-cli installer URL.

Pass -FromInstaller <https://...>, or set sigrok_cli.upstream.url in deps.json.
Upstream builds live at https://sigrok.org/download/binary/sigrok-cli/ (e.g.
sigrok-cli-NIGHTLY-x86_64-release-installer.exe). The default path is the
'sigrok runtime' workflow, which passes the deps.json URL for you.
'@
    }
    if ($installer -notmatch '^https?://') {
        throw "pack-sigrok-cli only accepts an http(s) installer URL; got '$installer'. Minting from a local file is not supported: the runtime is always fetched from the pinned URL."
    }
    $installerPath = Get-PinnedFile $installer $InstallerSha256 (Split-Path -Leaf $installer)
    if (-not $InstallerSha256 -and $upstream -and $upstream.sha256) {
        $actual = Get-Sha256 $installerPath
        if ($actual -ne $upstream.sha256) {
            Write-Warning "upstream installer changed (recorded $($upstream.sha256), fetched $actual); minting from the newer snapshot"
        }
    }
    Write-Step "installer: $installerPath"
    $extract = Join-Path $work 'installer'
    Write-Step 'unpack installer (7-Zip)'
    $src = Expand-NsisInstaller -Installer $installerPath -Dest $extract

    # ------------------------------------------------------------ normalise
    $normalised = Join-Path $work 'runtime'
    Write-Step 'normalise runtime (bin/ + decoders/ + COPYING + VERSION.txt)'
    Copy-SigrokRuntime -SourceRoot $src -Dest $normalised -SkipVersionProbe:$SkipVersionProbe

    # ------------------------------------------------------------ version
    $versionFile = Join-Path $normalised 'VERSION.txt'
    $probeText = if (Test-Path -LiteralPath $versionFile) { Get-Content -LiteralPath $versionFile -Raw } else { '' }
    $probe = ([string]$probeText).Trim()
    $detected = ''
    if ($probe -match '(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z._-]+)?)') { $detected = $Matches[1] }
    $pinned = $deps.sigrok_cli.version
    if (-not $Version) { $Version = if ($detected) { $detected } else { $pinned } }
    if (-not $Version) { throw 'cannot determine the sigrok-cli version; pass -Version' }
    if ($detected -and $pinned -and $detected -ne $pinned) {
        Write-Warning "this build is '$detected' but deps.json pins '$pinned'; the zip will be named $detected"
    }
    if ($probe -and $probe -notmatch 'unknown') { Write-Step "sigrok-cli version: $probe" }

    # ------------------------------------------------------------ compress
    $safeVersion = $Version -replace '[^0-9A-Za-z._-]', '-'
    $safeStamp = $Stamp -replace '[^0-9A-Za-z._-]', '-'
    $zipName = "sigrok-cli-$safeVersion$safeStamp-win64.zip"
    $zip = Join-Path $OutDir $zipName
    if ((Test-Path -LiteralPath $zip) -and -not $Force) {
        throw "$zip already exists; pass -Force to overwrite"
    }
    Write-Step "compress -> $zipName"
    Compress-Archive -Path (Join-Path $normalised '*') -DestinationPath $zip -CompressionLevel Optimal -Force

    # ------------------------------------------------------------ verify
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
    try { $entries = @($archive.Entries | ForEach-Object { $_.FullName -replace '\\', '/' }) }
    finally { $archive.Dispose() }
    foreach ($need in 'bin/sigrok-cli.exe', 'bin/python34.dll', 'bin/python34.zip', 'decoders/i2c/pd.py', 'VERSION.txt') {
        if ($entries -notcontains $need) { throw "archive is missing $need; bootstrap would fail on it" }
    }
    $decoderCount = @($entries | Where-Object { $_ -like 'decoders/*/pd.py' }).Count
    if ($decoderCount -lt 10) { throw "only $decoderCount decoders in the archive; source tree looks wrong" }
    if ($entries -contains 'decoders_ext/pd.py') { throw 'decoders_ext leaked into the archive; the upstream overlay belongs to the bundle' }

    $sha = (Get-Sha256 $zip).ToLowerInvariant()
    $sizeMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
    $installerSha = if ($installerPath) { (Get-Sha256 $installerPath).ToLowerInvariant() } else { '' }

    # ------------------------------------------------------------ deps.json
    if ($UpdateDeps -and -not $Url) {
        if (-not $Tag) { throw '-UpdateDeps needs -Url, or -Tag to derive the GitHub release URL' }
        $origin = ''
        try { $origin = (git -C $root remote get-url origin 2>$null | Select-Object -First 1) } catch { }
        if ($origin -and $origin -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(?:\.git)?/?$') {
            $repoPath = "$($Matches.owner)/$($Matches.repo)"
            $Url = "https://github.com/$repoPath/releases/download/$Tag/$zipName"
        } else {
            throw "cannot derive a release URL from origin ('$origin'); pass -Url explicitly"
        }
    }

    Write-Host ''
    Write-Host "  file      : $zip"
    Write-Host "  size      : $sizeMb MB"
    Write-Host "  sha256    : $sha"
    Write-Host "  version   : $Version"
    Write-Host "  decoders  : $decoderCount"
    if ($installerSha) { Write-Host "  installer : $installerSha" }
    Write-Host ''

    if (-not $UpdateDeps) {
        Write-Host '  paste into deps.json (sigrok_cli):'
        Write-Host "    `"version`": `"$Version`","
        Write-Host "    `"url`": `"$Url`","
        Write-Host "    `"sha256`": `"$sha`","
        Write-Host ''
    } else {
        $depsPath = Join-Path $root 'deps.json'
        $text = Get-Content -LiteralPath $depsPath -Raw
        $span = Get-JsonObjectSpan -Text $text -Key 'sigrok_cli'
        if (-not $span) { throw 'sigrok_cli block not found in deps.json' }
        $block = $text.Substring($span.Start, $span.Length)

        # Only the block's own keys are rewritten: the nested upstream object keeps
        # its own url/sha256, which describe the installer, not the repackaged zip.
        $indentMatch = [regex]::Match($block, '(?m)^(\s*)"version"\s*:')
        if (-not $indentMatch.Success) { throw 'cannot find the version key inside the sigrok_cli block' }
        $indent = $indentMatch.Groups[1].Value
        $block = Set-JsonScalar -Block $block -Indent $indent -Name 'version' -Value $Version
        $block = Set-JsonScalar -Block $block -Indent $indent -Name 'url' -Value $Url
        $block = Set-JsonScalar -Block $block -Indent $indent -Name 'sha256' -Value $sha

        if ($installerPath -and $installerSha) {
            $upstreamSpan = Get-JsonObjectSpan -Text $block -Key 'upstream'
            $upstreamUrl = if ($FromInstaller -match '^https?://') { $FromInstaller } else { $deps.sigrok_cli.upstream.url }
            if ($upstreamSpan -and $upstreamUrl) {
                $upstreamBlock = $block.Substring($upstreamSpan.Start, $upstreamSpan.Length)
                $upstreamIndent = [regex]::Match($upstreamBlock, '(?m)^(\s*)"url"').Groups[1].Value
                $upstreamBlock = Set-JsonScalar -Block $upstreamBlock -Indent $upstreamIndent -Name 'url' -Value $upstreamUrl
                $upstreamBlock = Set-JsonScalar -Block $upstreamBlock -Indent $upstreamIndent -Name 'sha256' -Value $installerSha
                $block = $block.Substring(0, $upstreamSpan.Start) + $upstreamBlock + $block.Substring($upstreamSpan.Start + $upstreamSpan.Length)
            }
        }

        $newText = $text.Substring(0, $span.Start) + $block + $text.Substring($span.Start + $span.Length)
        $null = $newText | ConvertFrom-Json
        Set-Content -LiteralPath $depsPath -Value $newText -Encoding utf8NoBOM -NoNewline
        Write-Step 'deps.json updated (sigrok_cli.version/url/sha256, upstream.url/sha256)'
    }

    Write-Host '  next:'
    $releaseTag = if ($Tag) { $Tag } else { '<tag>' }
    Write-Host "    1. gh release create $releaseTag --title 'sigrok-cli runtime' --notes 'Pinned sigrok-cli runtime'"
    Write-Host "       gh release upload $releaseTag `"$zip`""
    if (-not $UpdateDeps) {
        Write-Host '    2. set deps.json sigrok_cli.version/url/sha256 (see above)'
        Write-Host '    3. verify the pin:  pwsh scripts/bootstrap.ps1 -Force'
    } else {
        Write-Host '    2. commit deps.json, then verify: pwsh scripts/bootstrap.ps1 -Force'
    }
    Write-Host ''
}
finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
