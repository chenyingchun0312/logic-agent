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
function Find-SigrokRoot([string]$Hint = '') {
    $candidates = @($Hint, $env:SIGROK_CLI_ROOT, (Join-Path $script:DepsDir 'sigrok-cli'))
    if ($env:ZEPHYR_ENV_HOME) { $candidates += (Join-Path $env:ZEPHYR_ENV_HOME 'sigrok-cli') }
    $candidates += 'C:\Program Files\sigrok\sigrok-cli', 'C:\Program Files (x86)\sigrok\sigrok-cli'
    foreach ($c in ($candidates | Where-Object { $_ })) {
        if ((Test-Path -LiteralPath (Join-Path $c 'sigrok-cli.exe')) -and
            (Test-Path -LiteralPath (Join-Path $c 'share\libsigrokdecode\decoders\i2c\pd.py'))) {
            return (Resolve-Path $c).Path
        }
    }
    $null
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
function Copy-SigrokRuntime([string]$SourceRoot, [string]$Dest) {
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
    $version = (& (Join-Path $bin 'sigrok-cli.exe') --version 2>&1 | Select-Object -First 1)
    Set-Content -LiteralPath (Join-Path $Dest 'VERSION.txt') -Value $version -Encoding utf8
}
