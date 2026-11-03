# Shared helpers for bootstrap/build/test/release. Dot-source this file.
Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:DepsDir = Join-Path $script:RepoRoot '.deps'
$script:CacheDir = Join-Path $script:RepoRoot '.cache\downloads'

function Get-RepoRoot { $script:RepoRoot }

function Read-Deps {
    Get-Content -LiteralPath (Join-Path $script:RepoRoot 'deps.json') -Raw | ConvertFrom-Json
}

function Get-ProjectVersion {
    $init = Get-Content -LiteralPath (Join-Path $script:RepoRoot 'logic_agent\__init__.py') -Raw
    if ($init -notmatch '__version__\s*=\s*"([^"]+)"') { throw 'cannot read __version__ from logic_agent/__init__.py' }
    $Matches[1]
}

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

# Download with resume and mandatory SHA-256 check. Returns the cached path.
function Get-PinnedFile([string]$Url, [string]$Sha256, [string]$FileName) {
    if (-not $Url) { throw "no download URL for $FileName" }
    New-Item -ItemType Directory -Force -Path $script:CacheDir | Out-Null
    $target = Join-Path $script:CacheDir $FileName
    if ((Test-Path -LiteralPath $target) -and $Sha256 -and (Get-Sha256 $target) -eq $Sha256) { return $target }
    Write-Step "download $Url"
    & curl.exe -fL --retry 5 --retry-delay 3 -C - -o $target $Url
    if ($LASTEXITCODE -ne 0) {
        # A corrupt partial file can make resume fail forever; retry once from scratch.
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        & curl.exe -fL --retry 5 --retry-delay 3 -o $target $Url
        if ($LASTEXITCODE -ne 0) { throw "download failed ($LASTEXITCODE): $Url" }
    }
    $actual = Get-Sha256 $target
    if ($Sha256 -and $actual -ne $Sha256) {
        Remove-Item -LiteralPath $target -Force
        throw "SHA-256 mismatch for ${FileName}: expected $Sha256, got $actual"
    }
    if (-not $Sha256) { Write-Warning "$FileName is not pinned; sha256=$actual" }
    $target
}

# MSYS2 UCRT64 provides gcc, cmake, ninja, pkg-config, glib, libusb and zlib.
function Find-Msys2([string]$Hint = '') {
    $candidates = @($Hint, $env:MSYS2_ROOT, (Join-Path $script:DepsDir 'msys64'),
        (Join-Path $script:RepoRoot '.toolchains\msys64'), 'C:\msys64') | Where-Object { $_ }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $c 'usr\bin\bash.exe')) { return (Resolve-Path $c).Path }
    }
    $null
}

function Test-Msys2Ready([string]$Root) {
    $bin = Join-Path $Root 'ucrt64\bin'
    foreach ($exe in 'gcc.exe', 'cmake.exe', 'ninja.exe', 'pkg-config.exe', 'objdump.exe') {
        if (-not (Test-Path -LiteralPath (Join-Path $bin $exe))) { return $false }
    }
    foreach ($pc in 'glib-2.0.pc', 'libusb-1.0.pc', 'zlib.pc') {
        if (-not (Test-Path -LiteralPath (Join-Path $Root "ucrt64\lib\pkgconfig\$pc"))) { return $false }
    }
    $true
}

# sigrok-cli.exe is statically linked; it only needs its Python 3.4 runtime
# and the libsigrokdecode decoder tree next to it.
# Locate a top-level-or-nested JSON object assigned to "Key" and return its span
# ({ Start; Length }), matched by brace counting so nested objects stay intact.
function Get-JsonObjectSpan([string]$Text, [string]$Key) {
    $m = [regex]::Match($Text, '"' + [regex]::Escape($Key) + '"\s*:\s*\{')
    if (-not $m.Success) { return $null }
    $start = $m.Index + $m.Length - 1
    $depth = 0
    $inString = $false
    $escaped = $false
    for ($i = $start; $i -lt $Text.Length; $i++) {
        $c = $Text[$i]
        if ($inString) {
            if ($escaped) { $escaped = $false }
            elseif ($c -eq '\') { $escaped = $true }
            elseif ($c -eq '"') { $inString = $false }
            continue
        }
        if ($c -eq '"') { $inString = $true; continue }
        if ($c -eq '{') { $depth++ }
        elseif ($c -eq '}') {
            $depth--
            if ($depth -eq 0) { return @{ Start = $start; Length = $i - $start + 1 } }
        }
    }
    $null
}

# Replace a string value that sits at exactly <Indent> inside a JSON block, leaving
# deeper-indented keys of the same name (nested objects) alone.
function Set-JsonScalar([string]$Block, [string]$Indent, [string]$Name, [string]$Value) {
    $pattern = '(?m)^' + [regex]::Escape($Indent) + '"' + [regex]::Escape($Name) + '"\s*:\s*"[^"]*"'
    $replacement = $Indent + '"' + $Name + '": "' + $Value + '"'
    [regex]::Replace($Block, $pattern, $replacement.Replace('$', '$$'))
}

# sigrok.org ships the Windows sigrok-cli build as an NSIS installer (no zip, no
# GitHub releases), so mirroring its runtime starts with unpacking that .exe.
function Find-7Zip {
    $candidates = @()
    foreach ($name in '7z.exe', '7za.exe', '7zz.exe') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    foreach ($base in $env:ProgramFiles, ${env:ProgramFiles(x86)}) {
        if ($base) { $candidates += (Join-Path $base '7-Zip\7z.exe') }
    }
    $candidates += 'C:\Program Files\7-Zip\7z.exe'
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    $null
}

# Unpack a sigrok-cli NSIS installer into <Dest> and check it looks like a
# runtime root (sigrok-cli.exe + share\libsigrokdecode\decoders\i2c\pd.py).
function Expand-NsisInstaller([string]$Installer, [string]$Dest) {
    if (-not (Test-Path -LiteralPath $Installer)) { throw "installer not found: $Installer" }
    $sevenZip = Find-7Zip
    if (-not $sevenZip) {
        throw @'
7-Zip is required to unpack the sigrok-cli installer: sigrok.org only publishes
NSIS .exe builds (there is no zip and no GitHub binary release). Install 7-Zip
(https://7-zip.org); the 'sigrok runtime' workflow installs it on the runner, so
minting never depends on a developer machine.
'@
    }
    if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    # Capture 7-Zip's banner/progress: emitting it here would make the function
    # return the log lines instead of $Dest (the caller then saw a bogus "drive").
    $log = & $sevenZip x -y -bb0 "-o$Dest" $Installer 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip failed to extract $Installer (exit $LASTEXITCODE): $($log -join ' | ')"
    }
    foreach ($need in 'sigrok-cli.exe', 'share\libsigrokdecode\decoders\i2c\pd.py') {
        if (-not (Test-Path -LiteralPath (Join-Path $Dest $need))) {
            throw "extracted tree has no ${need}: $Dest"
        }
    }
    $Dest
}

function Get-DepPaths {
    [pscustomobject]@{
        Dsview      = Join-Path $script:DepsDir 'dsview'
        DsviewRes   = Join-Path $script:DepsDir 'dsview\DSView\res'
        Firmware    = Join-Path $script:DepsDir 'firmware\DSLogicPlus.fw'
        FirmwareLic = Join-Path $script:DepsDir 'firmware\license.txt'
        PythonEmbed = Join-Path $script:DepsDir 'python-embed'
        # Minimal sigrok-cli runtime (bin/) plus upstream decoders overlaid with decoders_ext/.
        SigrokBin   = Join-Path $script:DepsDir 'sigrok\bin'
        Decoders    = Join-Path $script:DepsDir 'sigrok\decoders'
        DslabBuild  = Join-Path $script:RepoRoot 'dslab\build'
        DslabExe    = Join-Path $script:RepoRoot 'dslab\build\dslab.exe'
    }
}

# Copy our adapted decoders over the upstream tree so edits in decoders_ext/
# are picked up by tests without re-running bootstrap.
function Sync-DecoderOverlay {
    $p = Get-DepPaths
    if (-not (Test-Path -LiteralPath $p.Decoders)) { return }
    foreach ($dir in Get-ChildItem -LiteralPath (Join-Path $script:RepoRoot 'decoders_ext') -Directory) {
        $target = Join-Path $p.Decoders $dir.Name
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Copy-Item -LiteralPath $dir.FullName -Destination $target -Recurse
        Get-ChildItem -LiteralPath $target -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    }
}

# Environment for running dslab, logic_agent and the decoder tests from the source tree.
function Set-DevEnvironment {
    $p = Get-DepPaths
    if (Test-Path -LiteralPath $p.DslabExe) { $env:DSLAB_EXE = $p.DslabExe }
    if (Test-Path -LiteralPath $p.DsviewRes) { $env:DSLAB_RES_DIR = $p.DsviewRes }
    if (Test-Path -LiteralPath $p.Firmware) { $env:DSLAB_FIRMWARE = $p.Firmware }
    $sigrok = Join-Path $p.SigrokBin 'sigrok-cli.exe'
    if (Test-Path -LiteralPath $sigrok) {
        $env:LOGIC_AGENT_DECODER_EXE = $sigrok
        $env:SIGROKDECODE_DIR = $p.Decoders
    }
    $p
}

# Files sigrok-cli.exe needs at runtime: the embedded Python 3.4 and the
# extension modules decoders actually import. Everything else in the
# installer (GUI-less Tk, test modules, Zadig) is left out.
$script:SigrokRuntimeFiles = @(
    'sigrok-cli.exe', 'python34.dll', 'python34.zip', 'msvcp100.dll', 'msvcr100.dll',
    '_bz2.pyd', '_ctypes.pyd', '_decimal.pyd', '_elementtree.pyd', '_hashlib.pyd',
    '_lzma.pyd', '_multiprocessing.pyd', '_socket.pyd', '_sqlite3.pyd', '_ssl.pyd',
    'pyexpat.pyd', 'select.pyd', 'unicodedata.pyd', 'winsound.pyd'
)

# Normalise a sigrok-cli installation into <Dest>\bin and <Dest>\decoders.
# -SkipVersionProbe is for packing a mirror on a machine where the binary cannot
# execute (wrong arch, blocked by policy); the shipped decoder tests are the real
# gate, VERSION.txt is informational.
function Copy-SigrokRuntime([string]$SourceRoot, [string]$Dest, [switch]$SkipVersionProbe) {
    $bin = Join-Path $Dest 'bin'
    $decoders = Join-Path $Dest 'decoders'
    if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $bin, $decoders | Out-Null
    foreach ($f in $script:SigrokRuntimeFiles) {
        $src = Join-Path $SourceRoot $f
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $bin }
        elseif ($f -in 'sigrok-cli.exe', 'python34.dll', 'python34.zip') { throw "sigrok-cli runtime incomplete, missing $src" }
    }
    $upstream = Join-Path $SourceRoot 'share\libsigrokdecode\decoders'
    Get-ChildItem -LiteralPath $upstream -Directory | Copy-Item -Destination $decoders -Recurse
    Get-ChildItem -LiteralPath $decoders -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    $copying = Join-Path $SourceRoot 'COPYING'
    if (Test-Path -LiteralPath $copying) { Copy-Item -LiteralPath $copying -Destination (Join-Path $Dest 'COPYING') }
    Write-SigrokVersionFile -RuntimeDest $Dest -SkipProbe:$SkipVersionProbe
}

# Writes VERSION.txt next to a normalised runtime by asking sigrok-cli for its
# version. The probe fails (exit 0xC0000135) when MSVCR100.dll is absent, so the
# mint step calls this again after it has added the MSVC runtime.
function Write-SigrokVersionFile([string]$RuntimeDest, [switch]$SkipProbe) {
    $bin = Join-Path $RuntimeDest 'bin'
    $version = 'unknown (version probe skipped)'
    if (-not $SkipProbe) {
        $probeOut = (& (Join-Path $bin 'sigrok-cli.exe') --version 2>&1 | Select-Object -First 1)
        $probeExit = $LASTEXITCODE
        $version = if ($null -ne $probeOut -and ([string]$probeOut).Trim()) {
            [string]$probeOut
        } else {
            "unknown (version probe produced no output, exit $probeExit)"
        }
    }
    Set-Content -LiteralPath (Join-Path $RuntimeDest 'VERSION.txt') -Value $version -Encoding utf8
}
