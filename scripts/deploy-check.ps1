param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipHardware,
    [switch]$SkipMcp
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$bin = Join-Path $Root 'bin'
# The packaged layout puts dslab.exe in bin/; the source layout builds it under
# dslab/build/. Resolve either so the same self-check works for developers.
$exe = @(
    (Join-Path $bin 'dslab.exe'),
    (Join-Path $Root 'dslab\build\dslab.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $exe) { $exe = Join-Path $bin 'dslab.exe' }
$python = @(
    (Join-Path $Root 'python\python.exe'),
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
$app = @(
    (Join-Path $Root 'app'),
    (Join-Path $Root '..')
) | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'logic_agent\mcp_server.py') } | Select-Object -First 1
$checks = [ordered]@{}

function Add-Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    $checks[$Name] = [pscustomobject]@{ ok = $Ok; detail = $Detail }
}

Add-Check 'dslab.exe' (Test-Path $exe) $exe
Add-Check 'runtime-dlls' ((Get-ChildItem (Split-Path -Parent $exe) -Filter '*.dll' | Measure-Object).Count -ge 6) 'expected glib/usb/zlib runtime'
Add-Check 'python' (Test-Path $python) "bundled=$python"
Add-Check 'app' ([bool]$app -and (Test-Path (Join-Path $app 'logic_agent\mcp_server.py'))) $app
$sigrok = Join-Path $bin 'sigrok-cli.exe'
$decoderDir = Join-Path $Root 'decoders'
Add-Check 'sigrok-cli' (Test-Path -LiteralPath $sigrok) $sigrok
Add-Check 'decoders' ((Test-Path (Join-Path $decoderDir 'i2c\pd.py')) -and (Test-Path (Join-Path $decoderDir 'i2s\pd.py')) -and (Test-Path (Join-Path $decoderDir 'spi\pd.py'))) $decoderDir
Add-Check 'smart_qspi' (Test-Path (Join-Path $decoderDir 'smart_qspi\pd.py')) (Join-Path $decoderDir 'smart_qspi')
$expanded = @('can', 'jtag', 'swd', 'onewire_link', 'ps2', 'mdio', 'ir_nec', 'spdif', 'usb_signalling', 'cjtag', 'sdcard_sd', 'spiflash', 'sdcard_spi', 'modbus', 'lin', 'onewire_network', 'jtag_stm32', 'jtag_ejtag')
$missingExpanded = @($expanded | Where-Object { -not (Test-Path -LiteralPath (Join-Path $decoderDir "$_\pd.py")) })
Add-Check 'expanded-decoders' ($missingExpanded.Count -eq 0) ($(if ($missingExpanded.Count) { 'missing: ' + ($missingExpanded -join ', ') } else { '18 direct/stacked decoders present' }))

$env:DSLAB_EXE = $exe
$env:DSLAB_RES_DIR = (Join-Path $bin 'res')
$env:PYTHONPATH = $app
$env:LOGIC_AGENT_DECODER_EXE = $sigrok
$env:SIGROKDECODE_DIR = $decoderDir

$doctor = & $exe doctor --json 2>&1 | Out-String
# Without a device doctor reports ok=false by design; still require the
# library and DSView resources to load so a broken bundle is caught.
$doctorOk = if ($SkipHardware) { $doctor -match '"lib_init":true' } else { $doctor -match '"ok":true' }
Add-Check 'doctor-json' $doctorOk $doctor.Trim()

if (-not $SkipHardware) {
    $devices = & $exe devices --json 2>&1 | Out-String
    Add-Check 'device-visible' ($devices -match 'DSLogic') $devices.Trim()
    $capDir = Join-Path $Root 'runs\deploy-smoke'
    $cap = & $exe capture once --channels D0 --rate 10k --samples 2000 --out $capDir --json 2>&1 | Out-String
    Add-Check 'capture' ($cap -match '"ok":true' -and $cap -match '"sample_count":[1-9]') $cap.Trim()
}

if (-not $SkipMcp) {
    $request = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}'
    $reply = $request | & $python -m logic_agent.mcp_server 2>&1 | Out-String
    Add-Check 'mcp-initialize' ($reply -match 'logic-agent') $reply.Trim()

    $capabilityRequest = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"capabilities","arguments":{}}}'
    $requests = Join-Path $Root 'runs\deploy-smoke\mcp-requests.jsonl'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $requests) | Out-Null
    Set-Content -Encoding ascii -LiteralPath $requests @($request, $capabilityRequest)
    $capabilityOut = Join-Path $Root 'runs\deploy-smoke\mcp-reply.jsonl'
    cmd /c "`"$python`" -m logic_agent.mcp_server < `"$requests`" > `"$capabilityOut`" 2>&1"
    $capabilityReply = if (Test-Path -LiteralPath $capabilityOut) { Get-Content -LiteralPath $capabilityOut -Raw } else { '' }
    $protocolCount = 0
    try {
        $messages = @(Get-Content -LiteralPath $capabilityOut -ErrorAction Stop | ForEach-Object {
            if ($_.Trim()) { $_ | ConvertFrom-Json }
        })
        $capability = $messages | Where-Object {
            $_.id -eq 2 -and $_.result.structuredContent.protocol_decoding.supported_protocols
        } | Select-Object -First 1
        if ($capability) {
            $protocolCount = @($capability.result.structuredContent.protocol_decoding.supported_protocols).Count
        }
    } catch {
        $protocolCount = 0
    }
    Add-Check 'mcp-capabilities-22-protocols' ($protocolCount -eq 22) "count=$protocolCount" 

    # One-command client setup. Runs against a throwaway home so the real user
    # config is never touched, then proves the launch spec is self-contained:
    # no cmd.exe, no DSLAB_* env, absolute paths only.
    $fakeHome = Join-Path $Root 'runs\mcp-install-home'
    foreach ($dir in '.codex', '.kiro') { New-Item -ItemType Directory -Force -Path (Join-Path $fakeHome $dir) | Out-Null }
    $setupText = & $python -m logic_agent.cli mcp-install --home $fakeHome --no-verify --json 2>&1 | Out-String
    $setup = $null
    try { $setup = $setupText | ConvertFrom-Json } catch { $setup = $null }
    $codexConfig = Join-Path $fakeHome '.codex\config.toml'
    $kiroConfig = Join-Path $fakeHome '.kiro\settings\mcp.json'
    $kiroEntry = $null
    if (Test-Path -LiteralPath $kiroConfig) {
        $kiroEntry = (Get-Content -LiteralPath $kiroConfig -Raw | ConvertFrom-Json).mcpServers.'logic-analyzer'
    }
    $launchOk = $false
    if ($setup) {
        $command = [string]$setup.launch.command
        $launchOk = (Test-Path -LiteralPath $command) -and
            ($command -like '*python*.exe') -and
            (@($setup.launch.args) -contains 'logic_agent.mcp_server') -and
            (@($setup.launch.args) -contains '--data-dir')
    }
    $entryOk = $null -ne $kiroEntry -and $kiroEntry.command -eq $setup.launch.command -and
        (@($kiroEntry.args) -contains 'logic_agent.mcp_server')
    Add-Check 'mcp-install' ([bool]$setup -and $setup.ok -and $launchOk -and $entryOk -and (Test-Path -LiteralPath $codexConfig)) $setupText.Trim()
    Remove-Item -LiteralPath $fakeHome -Recurse -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $sigrok) {
    $decoderList = & $sigrok -L 2>&1 | Out-String
    Add-Check 'decoder-runtime' ($decoderList -match 'smart_qspi' -and $decoderList -match 'i2c' -and $decoderList -match 'can' -and $decoderList -match 'spiflash' -and $decoderList -match 'modbus') 'sigrok-cli -L'

    # Offline decoder smoke test: no hardware and no external dependencies.
    # Build a tiny I2C transaction by hand and require a deterministic ACK.
    $probeDir = Join-Path $Root 'runs\decode-smoke'
    New-Item -ItemType Directory -Force -Path $probeDir | Out-Null
    $probe = Join-Path $probeDir 'i2c-probe.bin'
    $sc = New-Object System.Collections.Generic.List[byte]
    $sd = New-Object System.Collections.Generic.List[byte]
    for ($i = 0; $i -lt 4; $i++) { $sc.Add(1); $sd.Add(1) }
    $sc.Add(1); $sc.Add(1); $sd.Add(1); $sd.Add(0)
    foreach ($value in @(0xA0, 0x4B)) {
        for ($bit = 7; $bit -ge 0; $bit--) {
            $v = if ((($value -shr $bit) -band 1) -ne 0) { 1 } else { 0 }
            $sd.Add($v); $sd.Add($v); $sd.Add($v)
            $sc.Add(0); $sc.Add(1); $sc.Add(0)
        }
        $sd.Add(0); $sd.Add(0); $sd.Add(0)
        $sc.Add(0); $sc.Add(1); $sc.Add(0)
    }
    $sd.Add(0); $sd.Add(1); $sc.Add(1); $sc.Add(1)
    for ($i = 0; $i -lt 4; $i++) { $sc.Add(1); $sd.Add(1) }
    $bytes = New-Object byte[] $sc.Count
    for ($i = 0; $i -lt $sc.Count; $i++) { $bytes[$i] = [byte](($sc[$i] -band 1) -bor (($sd[$i] -band 1) -shl 1)) }
    [System.IO.File]::WriteAllBytes($probe, $bytes)
    $decoded = & $sigrok -i $probe -I 'binary:numchannels=2:samplerate=1000000' -P 'i2c:scl=0:sda=1' -A i2c --protocol-decoder-ann-class 2>&1 | Out-String
    Add-Check 'i2c-offline-decode' ($decoded -match 'Address write: 50' -and $decoded -match 'Data write: 4B' -and $decoded -match 'ACK') $decoded.Trim()
}

$failed = @($checks.GetEnumerator() | Where-Object { -not $_.Value.ok })
$result = [pscustomobject]@{
    ok = ($failed.Count -eq 0)
    root = $Root
    checks = $checks
}
$result | ConvertTo-Json -Depth 6
if ($failed.Count -gt 0) { exit 1 }
