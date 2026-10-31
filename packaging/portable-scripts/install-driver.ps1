param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$OpenZadig
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$driverDir = Join-Path $Root 'driver\DSView-Drivers'
$dpinst = Join-Path $driverDir 'dpinst-amd64.exe'
$zadig = Join-Path $Root 'driver\zadig.exe'
Write-Host 'Installs WinUSB for DreamSourceLab USB VID_2A0E devices.' -ForegroundColor Cyan
Write-Host 'Close DSView and unplug other capture software first.' -ForegroundColor Yellow

# Public bundles ship only Zadig; the DreamSourceLab driver package is
# included only in bundles built on a machine with DSView installed.
if (-not $OpenZadig -and -not (Test-Path -LiteralPath $dpinst)) {
    Write-Host 'DreamSourceLab driver package is not bundled; using Zadig instead.' -ForegroundColor Yellow
    $OpenZadig = $true
}
if ($OpenZadig) {
    if (-not (Test-Path -LiteralPath $zadig)) {
        throw "Zadig not found: $zadig. Install DSView (it installs the driver) or download Zadig from https://zadig.akeo.ie/"
    }
    Start-Process -FilePath $zadig
    Write-Host 'In Zadig: Options > List All Devices, select the DSLogic device (USB ID 2A0E 0020),' -ForegroundColor Cyan
    Write-Host 'choose WinUSB and click Install Driver. Do not select unrelated USB devices.' -ForegroundColor Cyan
    exit 0
}
$p = Start-Process -FilePath $dpinst -ArgumentList @('/S', '/SE') -Verb RunAs -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "driver installer exited with $($p.ExitCode); retry with -OpenZadig" }
Write-Host 'Driver installer completed. Run CHECK.cmd.' -ForegroundColor Green
