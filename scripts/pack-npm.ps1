#Requires -Version 7.2
<#
.SYNOPSIS
  Turn the verified portable bundle into an npm package (tarball ready to publish).

.DESCRIPTION
  Input is the bundle produced by scripts/release.ps1 (dist/stage/logic-agent).
  Output is dist/npm/package (the exact tree npm publishes) plus
  dist/npm/<name>-<version>.tgz.

  The npm package is a thin shell: the bundle tree verbatim, a package.json built
  from packaging/npm/package.json.in, and the Node launcher that starts the
  bundled Python MCP server. No postinstall script, no download at install time.

  Version comes from logic_agent/__init__.py (the single version source), so the
  package can never drift from the code.

.EXAMPLE
  pwsh scripts/release.ps1 -NoDsviewDriver -NoDriver
  pwsh scripts/pack-npm.ps1 -SelfTest

.EXAMPLE
  # Fast local loop: assemble from any directory, no bundle gates, only check that
  # the launcher can start Python.
  pwsh scripts/pack-npm.ps1 -Stage build/fake-stage -SkipVerify -SelfTest
#>
param(
    [string]$Stage = '',
    [string]$OutDir = '',
    [string]$Name = 'logic-agent-mcp',
    [switch]$SkipVerify,
    [switch]$SelfTest,
    [switch]$NoPack
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')

$root = Get-RepoRoot
$Version = Get-ProjectVersion
if (-not $Stage) { $Stage = Join-Path $root 'dist\stage\logic-agent' }
if (-not $OutDir) { $OutDir = Join-Path $root 'dist\npm' }
$Stage = [IO.Path]::GetFullPath($Stage)
$OutDir = [IO.Path]::GetFullPath($OutDir)
$pkg = Join-Path $OutDir 'package'

if ($Name -notmatch '^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$') {
    throw "invalid npm package name: '$Name' (must be lowercase, URL-safe)"
}
if (-not (Test-Path -LiteralPath $Stage)) {
    throw "bundle not found: $Stage`nRun scripts/release.ps1 first, or pass -Stage/-SkipVerify."
}
if (-not (Test-Path -LiteralPath (Join-Path $Stage 'runtime'))) {
    throw "not a bundle (no runtime/): $Stage"
}

# ---------------------------------------------------------------- preflight
# These are the files that make the package self-contained. A bundle missing any
# of them would install fine and then fail on the user's machine, which is the
# worst possible failure mode for an npm package.
$required = @(
    'runtime\python\python.exe',
    'runtime\app\logic_agent\mcp_server.py',
    'runtime\app\logic_agent\__init__.py',
    'runtime\bin\dslab.exe',
    'runtime\bin\sigrok-cli.exe',
    'runtime\decoders\i2c\pd.py',
    'runtime\licenses',
    'THIRD-PARTY-NOTICES.md'
)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Stage $_)) })
if ($missing.Count -gt 0) {
    $list = ($missing | ForEach-Object { "  $_" }) -join "`n"
    if ($SkipVerify) {
        Write-Warning "bundle is incomplete (continuing because -SkipVerify):`n$list"
    } else {
        throw "bundle is incomplete; refusing to publish a package that would fail at runtime:`n$list`nRebuild with scripts/release.ps1."
    }
}
$bundleVersionFile = Join-Path $Stage 'VERSION.txt'
if (Test-Path -LiteralPath $bundleVersionFile) {
    $bundleVersion = (Get-Content -LiteralPath $bundleVersionFile -Raw).Trim()
    if ($bundleVersion -notmatch [regex]::Escape($Version)) {
        Write-Warning "bundle VERSION.txt is '$bundleVersion' but the package version is '$Version' (stale stage?)"
    }
}

# ---------------------------------------------------------------- assemble
Write-Step "assemble npm package $Name@$Version"
if (Test-Path -LiteralPath $OutDir) { Remove-Item -LiteralPath $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pkg, (Join-Path $pkg 'launcher') | Out-Null
Get-ChildItem -LiteralPath $Stage -Force | Copy-Item -Destination $pkg -Recurse -Force

# Byte-compiled files are machine- and interpreter-specific; never ship them.
$cacheDirs = @(Get-ChildItem -LiteralPath $pkg -Recurse -Force -Directory -Filter '__pycache__')
if ($cacheDirs.Count -gt 0) {
    Write-Step "drop $($cacheDirs.Count) __pycache__ directories"
    $cacheDirs | Remove-Item -Recurse -Force
}
Get-ChildItem -LiteralPath $pkg -Recurse -Force -File -Filter '*.pyc' | Remove-Item -Force

$template = Get-Content -LiteralPath (Join-Path $root 'packaging\npm\package.json.in') -Raw
$packageJson = $template.Replace('@@NAME@@', $Name).Replace('@@VERSION@@', $Version)
if ($packageJson -match '@@') { throw "unreplaced placeholder in package.json template: $($Matches[0])" }
$null = $packageJson | ConvertFrom-Json   # fail here, not at npm publish
Set-Content -LiteralPath (Join-Path $pkg 'package.json') -Value $packageJson -Encoding utf8NoBOM -NoNewline

Copy-Item -LiteralPath (Join-Path $root 'packaging\npm\launcher\logic-agent-mcp.mjs') -Destination (Join-Path $pkg 'launcher') -Force
# The bundle README documents the zip; npm users need the npx story instead.
Copy-Item -LiteralPath (Join-Path $root 'packaging\npm\README.npm.md') -Destination (Join-Path $pkg 'README.md') -Force
$rootLicense = Join-Path $root 'LICENSE'
if (Test-Path -LiteralPath $rootLicense) { Copy-Item -LiteralPath $rootLicense -Destination (Join-Path $pkg 'LICENSE') -Force }

$launcher = Join-Path $pkg 'launcher\logic-agent-mcp.mjs'
# Prefer the .exe/.cmd shims: resolving to node.ps1 (some Windows toolchains put
# PowerShell wrappers on PATH ahead of the real binaries) makes every call go
# through a shell host and breaks output redirection.
$nodeCmd = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCmd) { $nodeCmd = Get-Command node -ErrorAction SilentlyContinue }
if (-not $nodeCmd) { throw 'node not found on PATH (needed to build and self-test the package)' }
$nodePath = $nodeCmd.Source

# The bundle's checksums.sha256 was written before package.json/launcher/README
# existed here, so the packaged tree needs its own manifest - otherwise
# VERIFY-FILES.cmd would report a mismatch on the README it replaced.
# Same format as scripts/release.ps1's Write-Checksums so verify-files.py reads it.
Write-Step 'regenerate checksums.sha256 for the package tree'
$sumFiles = @(Get-ChildItem -LiteralPath $pkg -Recurse -File | Where-Object { $_.Name -ne 'checksums.sha256' } | Sort-Object FullName)
$sumLines = foreach ($f in $sumFiles) {
    '{0}  {1}' -f (Get-Sha256 $f.FullName), $f.FullName.Substring($pkg.Length + 1).Replace('\', '/')
}
[IO.File]::WriteAllLines((Join-Path $pkg 'checksums.sha256'), [string[]]$sumLines, [Text.Encoding]::ASCII)

# ---------------------------------------------------------------- self test
if ($SelfTest) {
    Write-Step 'self-test: launcher --version'
    $reported = (& $nodePath $launcher --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "launcher exited $LASTEXITCODE`n$reported" }
    if ($reported -ne $Version) { throw "launcher reported '$reported', expected '$Version'" }

    Write-Step 'self-test: MCP initialize over stdio'
    $tmpData = Join-Path ([IO.Path]::GetTempPath()) ("logic-agent-npm-test-" + [guid]::NewGuid().ToString('N'))
    try {
        $request = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"pack-npm","version":"1"}}}'
        $response = ($request | & $nodePath $launcher --data-dir $tmpData 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) { throw "launcher exited $LASTEXITCODE during initialize`n$response" }
        if ($response -notmatch '"jsonrpc"\s*:\s*"2\.0"') {
            throw "no JSON-RPC response on stdout during initialize`n$response"
        }
        if ($response -match 'Traceback \(most recent call last\)') {
            throw "server raised during initialize`n$response"
        }
    } finally {
        if (Test-Path -LiteralPath $tmpData) { Remove-Item -LiteralPath $tmpData -Recurse -Force }
    }
    Write-Step 'self-test ok'
}

# ---------------------------------------------------------------- pack
if ($NoPack) {
    Write-Step "package tree ready: $pkg"
    return
}

Write-Step 'npm pack'
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) { $npmCmd = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npmCmd) { throw 'npm not found on PATH' }
$packOut = & $npmCmd.Source pack $pkg --pack-destination $OutDir --json 2>&1
if ($LASTEXITCODE -ne 0) { throw "npm pack failed:`n$($packOut | Out-String)" }
$tarball = Get-ChildItem -LiteralPath $OutDir -Filter '*.tgz' -File | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $tarball) { throw "npm pack produced no tarball in $OutDir`n$($packOut | Out-String)" }

$sizeMb = [math]::Round($tarball.Length / 1MB, 1)
Write-Step "package: $($tarball.FullName) ($sizeMb MB)"
if ($sizeMb -gt 120) { Write-Warning "tarball is $sizeMb MB; npm registry limit is 250 MB unpacked per package" }
Write-Host ''
Write-Host "  publish : npm publish $($tarball.FullName) --access public"
Write-Host "  client  : {`"command`": `"npx.cmd`", `"args`": [`"-y`", `"$Name`"]}"
Write-Host ''
