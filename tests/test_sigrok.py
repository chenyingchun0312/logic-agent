import os
from pathlib import Path

import pytest

from logic_agent.backends.sigrok import SigrokBackend, _parse_scan_line
from logic_agent.errors import CaptureError, ValidationError
from logic_agent.models import CaptureRequest, Trigger


def test_parse_modern_scan_line():
    fields = _parse_scan_line(
        "fx2lafw:conn=1.2 -- Saleae Logic 8 with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7"
    )
    assert fields["driver"] == "fx2lafw"
    assert fields["conn"] == "conn=1.2"
    assert fields["name"] == "Saleae Logic 8"
    assert fields["channels"].split() == [f"D{i}" for i in range(8)]


def test_parse_legacy_scan_line():
    fields = _parse_scan_line("driver: fx2lafw serial: abc123 channels: D0,D1")
    assert fields["driver"] == "fx2lafw"
    assert fields["serial"] == "abc123"
    assert fields["channels"] == "D0,D1"


@pytest.mark.skipif(os.name != "nt", reason="executable shim is Windows-specific")
def test_scan_header_is_ignored(tmp_path):
    script = tmp_path / "fake_sigrok.py"
    script.write_text(
        "import sys\n"
        "print('The following devices were found:')\n"
        "print('fx2lafw:conn=1.2 -- Demo Logic 8 with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7')\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "sigrok-cli.cmd"
    launcher.write_text(f'@echo off\r\n"{os.sys.executable}" "{script}" %*\r\n', encoding="ascii")
    devices = SigrokBackend(executable=str(launcher)).list_devices()
    assert len(devices) == 1
    assert devices[0].name == "Demo Logic 8"


FAKE_SIGROK = r'''#!{python}
import os, sys
args = sys.argv[1:]
if "--scan" in args:
    print("fx2lafw:conn=1.2 -- Demo Logic 16 with 16 channels: D0 D1 D2 D3 D4 D5 D6 D7 D8 D9 D10 D11 D12 D13 D14 D15")
elif "--help" in args:
    print("Usage: sigrok-cli [OPTION]...")
    print("  --trigger <trigger>  Trigger configuration")
else:
    if "--output-file" not in args:
        sys.exit(3)
    out = args[args.index("--output-file") + 1]
    count = int(args[args.index("--samples") + 1])
    channels = args[args.index("--channels") + 1].split(",")
    positions = [f"D{{i}}" for i in range(16)]
    with open(out, "wb") as handle:
        for sample in range(count):
            high = {{f"D{{i}}": i == sample for i in range(16)}}
            word = 0
            for name in channels:
                if high[name]:
                    word |= 1 << positions.index(name)
            handle.write(word.to_bytes(2, "little"))
'''


@pytest.mark.skipif(os.name != "nt", reason="executable shim is Windows-specific")
def test_sigrok_capture_with_two_byte_unitsize(tmp_path):
    script = tmp_path / "fake_sigrok.py"
    script.write_text(FAKE_SIGROK.format(python=os.sys.executable), encoding="utf-8")
    launcher = tmp_path / "sigrok-cli.cmd"
    launcher.write_text(f'@echo off\r\n"{os.sys.executable}" "{script}" %*\r\n', encoding="ascii")

    backend = SigrokBackend(executable=str(launcher))
    devices = backend.list_devices()
    assert devices[0].channels[0] == "D0"
    assert devices[0].channels[-1] == "D15"

    captured = backend.capture(
        CaptureRequest(
            channels=("D0", "D8"),
            sample_rate_hz=1_000_000,
            duration_s=0.001,
            trigger=Trigger("D0", "rising"),
        )
    )
    assert captured.sample_count == 1_000
    assert captured.samples["D0"][0] == 1
    assert captured.samples["D8"][0] == 0
    assert captured.samples["D0"][1] == 0
    assert captured.samples["D8"][8] == 1
    assert captured.trigger_applied is True
    assert captured.warnings == []


@pytest.mark.skipif(os.name != "nt", reason="executable shim is Windows-specific")
def test_sigrok_rejects_unknown_channel(tmp_path):
    script = tmp_path / "fake_sigrok.py"
    script.write_text(FAKE_SIGROK.format(python=os.sys.executable), encoding="utf-8")
    launcher = tmp_path / "sigrok-cli.cmd"
    launcher.write_text(f'@echo off\r\n"{os.sys.executable}" "{script}" %*\r\n', encoding="ascii")
    backend = SigrokBackend(executable=str(launcher))
    with pytest.raises(ValidationError):
        backend.capture(CaptureRequest(channels=("D99",), sample_rate_hz=1_000_000, duration_s=0.001))

