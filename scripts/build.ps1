#Requires -Version 7.2
<#
.SYNOPSIS
  Build dslab.exe with MSYS2 UCRT64 and copy its runtime DLLs next to it.

.DESCRIPTION
  Output: dslab/build/dslab.exe plus every non-system DLL it imports (found
  recursively with objdump). Run scripts/bootstrap.ps1 first.
#>
param(
    [string]$Msys2Root = '',
    [ValidateSet('Release', 'Debug', 'RelWithDebInfo')]
    [string]$Config = 'Release',
    [switch]$Clean
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$paths = Get-DepPaths

$msys = Find-Msys2 $Msys2Root
if (-not $msys -or -not (Test-Msys2Ready $msys)) { throw 'MSYS2 UCRT64 toolchain not ready; run scripts/bootstrap.ps1' }
if (-not (Test-Path -LiteralPath (Join-Path $paths.Dsview 'libsigrok4DSL'))) { throw 'DSView source missing; run scripts/bootstrap.ps1' }
$ucrt = Join-Path $msys 'ucrt64\bin'
$env:PATH = "$ucrt;$env:PATH"

$build = $paths.DslabBuild
if ($Clean -and (Test-Path -LiteralPath $build)) { Remove-Item -LiteralPath $build -Recurse -Force }

Write-Step "configure ($Config)"
& (Join-Path $ucrt 'cmake.exe') -S (Join-Path $root 'dslab') -B $build -G Ninja `
    "-DCMAKE_BUILD_TYPE=$Config" "-DDSVIEW_ROOT=$($paths.Dsview -replace '\\', '/')" | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'cmake configure failed' }
Write-Step 'build'
& (Join-Path $ucrt 'cmake.exe') --build $build | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'cmake build failed' }

# Copy every DLL dslab.exe pulls in from UCRT64 (glib, libusb, iconv, ...).
Write-Step 'collect runtime DLLs'
$objdump = Join-Path $ucrt 'objdump.exe'
$seen = @{}
$queue = [System.Collections.Generic.Queue[string]]::new()
$queue.Enqueue($paths.DslabExe)
while ($queue.Count) {
    $file = $queue.Dequeue()
    foreach ($line in (& $objdump -p $file | Select-String -Pattern 'DLL Name:\s*(\S+)')) {
        $dll = $line.Matches[0].Groups[1].Value
        if ($seen.ContainsKey($dll.ToLowerInvariant())) { continue }
        $seen[$dll.ToLowerInvariant()] = $true
        $src = Join-Path $ucrt $dll
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination $build -Force
            $queue.Enqueue($src)
        }
    }
}
$dlls = Get-ChildItem -LiteralPath $build -Filter '*.dll' | Select-Object -ExpandProperty Name

# Smoke test with PATH reset so a missing DLL is caught here, not on a colleague's PC.
$savedPath = $env:PATH
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
try {
    $help = & $paths.DslabExe --help 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $help -notmatch 'dslab') { throw "dslab.exe smoke test failed: $help" }
} finally { $env:PATH = $savedPath }

Write-Host ''
Write-Host "dslab.exe  $($paths.DslabExe)"
Write-Host "DLLs       $($dlls -join ', ')"
