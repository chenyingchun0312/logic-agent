param(
    [string]$Dslab = (Join-Path $PSScriptRoot '..\dslab\build\dslab.exe'),
    [string]$Out = (Join-Path $PSScriptRoot '..\runs\acceptance')
)
$ErrorActionPreference = 'Stop'
$Dslab = (Resolve-Path $Dslab).Path
$script:InvokeIndex = 0
function Invoke-Dslab([string[]]$Arguments) {
    $script:InvokeIndex++
    $stdout = Join-Path $env:TEMP ("dslab-$PID-$script:InvokeIndex.json")
    $stderr = Join-Path $env:TEMP ("dslab-$PID-$script:InvokeIndex.err")
    $process = Start-Process -FilePath $Dslab -ArgumentList $Arguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $text = if (Test-Path $stdout) { Get-Content -Raw $stdout } else { '' }
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    if (-not $text.Trim()) { throw "dslab returned no output: $($Arguments -join ' ')" }
    $json = ($text.Trim() -split "?
" | Select-Object -Last 1) | ConvertFrom-Json
    return @{ Code = $process.ExitCode; Json = $json; Raw = $text.Trim() }
}
$status = Invoke-Dslab @('firmware', 'status', '--json')
if (-not $status.Json.plus_present) {
    throw "DSLogic Plus (2A0E:0020) is not enumerated. Run 'dslab firmware status --json'; if uploaded=true, unplug and replug USB."
}
$doctor = Invoke-Dslab @('doctor', '--json')
if (-not $doctor.Json.ok) { throw "doctor failed: $($doctor.Raw)" }
$devices = Invoke-Dslab @('devices', '--json')
if (-not $devices.Json.devices -or $devices.Json.devices.Count -lt 1) { throw 'no DSLogic device listed' }
$caps = Invoke-Dslab @('capabilities', '--json')
if (-not $caps.Json.model) { throw "capabilities failed: $($caps.Raw)" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$runs = @()
foreach ($i in 1..2) {
    $dir = Join-Path $Out ("run-$i-" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
    $cap = Invoke-Dslab @('capture', 'once', '--channels', 'D0', '--rate', '1M', '--duration', '10ms', '--out', $dir, '--json')
    if (-not $cap.Json.ok) { throw "capture $i failed: $($cap.Raw)" }
    $meta = Get-Content -Raw (Join-Path $dir 'meta.json') | ConvertFrom-Json
    $rawInfo = Get-Item (Join-Path $dir 'capture.raw')
    $vcdInfo = Get-Item (Join-Path $dir 'capture.vcd')
    $expected = [math]::Ceiling($meta.sample_count * $meta.enabled_channels / 8)
    if ($rawInfo.Length -lt $expected) { throw "run $i raw too short: $($rawInfo.Length) < $expected" }
    if ($meta.sample_count -le 0) { throw "run $i produced zero samples" }
    if ($vcdInfo.Length -le 0) { throw "run $i VCD is empty" }
    $runs += [pscustomobject]@{
        run = $i
        samples = $meta.sample_count
        raw_bytes = $rawInfo.Length
        expected_min_bytes = $expected
        vcd_bytes = $vcdInfo.Length
        triggered = $meta.triggered
        overflow = $meta.overflow
        timed_out = $meta.timed_out
        sha256 = (Get-FileHash $rawInfo.FullName -Algorithm SHA256).Hash
        dir = $dir
    }
}
$result = [pscustomobject]@{
    ok = $true
    model = $caps.Json.model
    device = $devices.Json.devices[0].name
    sample_rate_hz = 1000000
    runs = $runs
    raw_identical = ($runs[0].sha256 -eq $runs[1].sha256)
}
$result | ConvertTo-Json -Depth 6
