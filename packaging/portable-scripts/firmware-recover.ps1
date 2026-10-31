param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$dslab = Join-Path $Root 'runtime\bin\dslab.exe'
$fw = Join-Path $Root 'firmware\DSLogicPlus.fw'
$env:DSLAB_RES_DIR = Join-Path $Root 'runtime\bin\res'
$env:DSLAB_FIRMWARE = $fw
if (-not (Test-Path -LiteralPath $dslab)) { throw "missing dslab.exe: $dslab" }
if (-not (Test-Path -LiteralPath $fw)) { throw "missing firmware: $fw" }
Write-Host 'Firmware recovery is only for a DSLogic Plus that enumerates as USB 2A0E:0001.' -ForegroundColor Yellow
$statusText = (& $dslab firmware status --json 2>&1 | Out-String).Trim()
Write-Host $statusText
if ($statusText -match '"plus_present":true') {
    Write-Host 'DSLogic Plus is already running normally; no firmware write is needed.' -ForegroundColor Green
    exit 0
}
if ($statusText -notmatch '"bootloader_present":true') {
    throw 'No DSLogic Plus or FX3 bootloader found. Check the cable, USB port and driver first.'
}
Write-Host ''
Write-Host 'The device is in bootloader mode. A firmware write is destructive; this tool will NOT do it automatically.' -ForegroundColor Yellow
Write-Host 'Run this command yourself after reviewing docs\FIRMWARE.md:' -ForegroundColor Cyan
Write-Host ("  `"{0}`" firmware install --file `"{1}`" --pid 0020 --json" -f $dslab, $fw)
Write-Host 'If uploaded=true but reenumerated=false, unplug and replug USB, then run FIRMWARE-STATUS.cmd.' -ForegroundColor Cyan

