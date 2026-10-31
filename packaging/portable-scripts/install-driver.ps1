param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$OpenZadig
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$driverDir = Join-Path $Root 'driver\DSView-Drivers'
$dpinst = Join-Path $driverDir 'dpinst-amd64.exe'
$zadig = Join-Path $Root 'driver\zadig.exe'
if (-not (Test-Path -LiteralPath $dpinst)) { throw "missing driver installer: $dpinst" }
Write-Host 'Installs WinUSB for DreamSourceLab USB VID_2A0E devices.' -ForegroundColor Cyan
Write-Host 'Close DSView and unplug other capture software first.' -ForegroundColor Yellow
if ($OpenZadig) {
    if (-not (Test-Path -LiteralPath $zadig)) { throw "missing Zadig: $zadig" }
    Start-Process -FilePath $zadig
    Write-Host 'Select the DSLogic device in Zadig and install WinUSB.' -ForegroundColor Cyan
    exit 0
}
$p = Start-Process -FilePath $dpinst -ArgumentList @('/S', '/SE') -Verb RunAs -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "driver installer exited with $($p.ExitCode); retry with -OpenZadig" }
Write-Host 'Driver installer completed. Run CHECK.cmd.' -ForegroundColor Green

