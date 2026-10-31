param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$runtime = Join-Path $Root 'runtime'
$dslab = Join-Path $runtime 'bin\dslab.exe'
if (-not (Test-Path -LiteralPath $dslab)) { throw "missing dslab.exe: $dslab" }
$out = Join-Path $Root 'data\selftest'
$env:DSLAB_RES_DIR = Join-Path $runtime 'bin\res'
$env:DSLAB_FIRMWARE = Join-Path $Root 'firmware\DSLogicPlus.fw'
& $dslab capture once --channels D0 --operation internal --samples 4096 --out $out --json
if ($LASTEXITCODE -ne 0) { throw "internal self-test failed with exit code $LASTEXITCODE" }

