#Requires -Version 7.2
<#
.SYNOPSIS
  Run pytest against the source tree with the decoder runtime from .deps/.

.EXAMPLE
  pwsh scripts/test.ps1
  pwsh scripts/test.ps1 -RequireDecoders            # fail instead of skipping decoder tests
  pwsh scripts/test.ps1 -- -k i2c                   # extra pytest arguments after --
#>
param(
    [switch]$RequireDecoders,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
Sync-DecoderOverlay
$null = Set-DevEnvironment
if (-not $env:LOGIC_AGENT_DECODER_EXE) {
    if ($RequireDecoders) { throw 'decoder runtime missing in .deps/sigrok; run scripts/bootstrap.ps1' }
    Write-Warning 'decoder runtime missing; protocol decoder tests will be skipped'
}
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }

# Use a repo-local temp dir: the shared %TEMP%\pytest-of-<user> is sometimes
# left with broken ACLs on Windows and makes pytest fail before any test runs.
$basetemp = Join-Path $root 'build\pytest'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $basetemp) | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
Push-Location $root
try {
    & $python -m pytest -q -rs -p no:cacheprovider --basetemp $basetemp @PytestArgs 2>&1 | Tee-Object -Variable lines | Out-Host
    $code = $LASTEXITCODE
} finally { Pop-Location }
# A green run that skipped the decoder tests would hide a broken decoder setup.
if ($code -eq 0 -and $RequireDecoders -and ($lines -match 'decoder runtime is not installed')) {
    Write-Error 'decoder tests were skipped although -RequireDecoders was given'
    exit 1
}
exit $code
