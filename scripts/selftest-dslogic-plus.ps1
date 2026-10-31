param(
    [string]$Dslab = (Join-Path $PSScriptRoot '..\dslab\build\dslab.exe'),
    [string]$Out = (Join-Path $PSScriptRoot '..\runs\selftest')
)
$ErrorActionPreference = 'Stop'
$Dslab = (Resolve-Path $Dslab).Path
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$dir = Join-Path $Out "internal-$stamp"

# This uses the device's own internal pattern generator. It validates the
# USB -> FPGA -> sample-stream -> raw/VCD path without external wiring.
$stdout = Join-Path $env:TEMP "dslab-selftest-$PID.json"
$stderr = Join-Path $env:TEMP "dslab-selftest-$PID.err"
$process = Start-Process -FilePath $Dslab -ArgumentList @(
    'capture', 'once', '--channels', 'D0', '--operation', 'internal',
    '--samples', '4096', '--out', $dir, '--json'
) -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$text = Get-Content -Raw $stdout
$errText = if (Test-Path $stderr) { Get-Content -Raw $stderr } else { '' }
Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
if (-not $text.Trim()) { throw "dslab returned no output: $errText" }
$meta = $text.Trim() | ConvertFrom-Json
if ($process.ExitCode -ne 0 -or -not $meta.ok) {
    throw "internal self-test capture failed: $($text.Trim()) $errText"
}
$raw = [System.IO.File]::ReadAllBytes($meta.raw_path)
if ($raw.Length -eq 0) { throw 'internal self-test produced an empty raw file' }

# DSLogic's internal test pattern is an alternating 1010... bit stream.
# With one enabled channel, every byte is therefore 0xAA or 0x55.
$first = $raw[0]
if ($first -ne 0xAA -and $first -ne 0x55) {
    throw ("internal pattern starts with unexpected byte 0x{0:X2}" -f $first)
}
$transitions = 0
$ones = 0
foreach ($byte in $raw) {
    if ($byte -ne $first) { throw ("internal pattern is not a stable alternating stream (0x{0:X2})" -f $byte) }
    for ($bit = 0; $bit -lt 8; $bit++) {
        $value = ($byte -shr $bit) -band 1
        $ones += $value
        if ($bit -gt 0) {
            $previous = ($byte -shr ($bit - 1)) -band 1
            if ($value -ne $previous) { $transitions++ }
        }
    }
}
if ($ones -eq 0) { throw 'internal pattern is stuck low; sampling path is suspect' }
if ($ones -eq ($raw.Length * 8)) { throw 'internal pattern is stuck high; sampling path is suspect' }
$transitions += $raw.Length - 1  # byte boundary between 0xAA / 0x55 streams is constant here
$vcd = Get-Item -LiteralPath $meta.vcd_path
if ($vcd.Length -eq 0) { throw 'internal self-test produced an empty VCD' }

[pscustomobject]@{
    ok = $true
    mode = 'internal-test-pattern'
    sample_rate_hz = $meta.sample_rate
    sample_count = $meta.sample_count
    raw_bytes = $raw.Length
    transitions = $transitions
    vcd_bytes = $vcd.Length
    run_dir = $meta.run_dir
} | ConvertTo-Json -Depth 5
