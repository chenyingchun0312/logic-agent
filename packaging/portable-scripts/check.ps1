param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipHardware,
    [switch]$SkipMcp
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$runtime = Join-Path $Root 'runtime'
$deploy = Join-Path $runtime 'scripts\deploy-check.ps1'
if (-not (Test-Path -LiteralPath $deploy)) { throw "runtime not found: $deploy" }
$env:DSLAB_FIRMWARE = Join-Path $Root 'firmware\DSLogicPlus.fw'
& $deploy -Root $runtime -SkipHardware:$SkipHardware -SkipMcp:$SkipMcp
exit $LASTEXITCODE

