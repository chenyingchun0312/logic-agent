import json
import subprocess

import pytest

from logic_agent.backends.dslab import DslabBackend, _unpack_cross
from logic_agent.errors import BackendUnavailableError
from logic_agent.models import CaptureRequest


def test_unpack_cross_bit_order_and_frame_layout():
    # 2 channels, 64 samples: frame 0 is [D0 block][D1 block].
    raw = bytearray(16)
    raw[0] = 0b00000101
    raw[8] = 0b00000010
    samples = _unpack_cross(bytes(raw), 64, 2)
    assert len(samples[0]) == 64 and len(samples[1]) == 64
    assert samples[0][0] == 1 and samples[0][1] == 0 and samples[0][2] == 1
    assert samples[1][1] == 1 and samples[1][0] == 0


class _FakeCompleted:
    def __init__(self, payload, returncode=0, stderr=""):
        self.stdout = json.dumps(payload) + "\n"
        self.stderr = stderr
        self.returncode = returncode


def test_backend_parses_cli_and_unpacks_raw(tmp_path, monkeypatch):
    captured = tmp_path / "cap"
    captured.mkdir()
    raw = bytes(16)
    (captured / "capture.raw").write_bytes(raw)
    exe = tmp_path / "dslab.exe"
    exe.write_bytes(b"stub")

    backend = DslabBackend(executable=str(exe))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "devices":
            return _FakeCompleted({"ok": True, "devices": [{"index": 0, "name": "DSLogic PLus", "handle": 7}]})
        return _FakeCompleted({
            "ok": True,
            "raw_path": str(captured / "capture.raw"),
            "vcd_path": "",
            "sample_rate": 1_000_000,
            "sample_limit": 64,
            "sample_count": 64,
            "raw_bytes": 16,
            "enabled_channels": 2,
            "triggered": False,
            "overflow": False,
            "timed_out": False,
        })

    monkeypatch.setattr(subprocess, "run", fake_run)
    request = CaptureRequest(
        channels=("D0", "D1"), sample_rate_hz=1_000_000, duration_s=64e-6
    )
    signal = backend.capture(request)
    assert signal.backend == "dslab"
    assert signal.sample_count == 64
    assert signal.samples["D0"][0] == 0
    assert signal.native_files["dslab-raw"].name == "capture.raw"
    assert calls[-1][1:3] == ["capture", "once"]
    assert calls[-1][0] == str(exe)
    assert calls[-1].count(str(exe)) == 1


def test_backend_sorts_channels_to_match_raw_block_order(tmp_path, monkeypatch):
    captured = tmp_path / "cap"
    captured.mkdir()
    raw = bytearray(16)
    raw[8] = 0b00000001  # frame 0, D3 block: sample 0 high
    (captured / "capture.raw").write_bytes(raw)
    exe = tmp_path / "dslab.exe"
    exe.write_bytes(b"stub")

    backend = DslabBackend(executable=str(exe))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "devices":
            return _FakeCompleted({"ok": True, "devices": [{"index": 0, "name": "DSLogic PLus", "handle": 7}]})
        return _FakeCompleted({
            "ok": True,
            "raw_path": str(captured / "capture.raw"),
            "vcd_path": "",
            "sample_rate": 1_000_000,
            "sample_limit": 64,
            "sample_count": 64,
            "raw_bytes": 16,
            "enabled_channels": 2,
            "triggered": False,
            "overflow": False,
            "timed_out": False,
        })

    monkeypatch.setattr(subprocess, "run", fake_run)
    request = CaptureRequest(
        channels=("D3", "D0"), sample_rate_hz=1_000_000, duration_s=64e-6
    )
    signal = backend.capture(request)
    assert calls[-1][calls[-1].index("--channels") + 1] == "D0,D3"
    assert signal.channels[0].id == "D0"
    assert signal.channels[1].id == "D3"


def test_backend_rejects_missing_binary():
    backend = DslabBackend(executable="C:/definitely/not/here/dslab.exe")
    with pytest.raises(BackendUnavailableError):
        backend.list_devices()


def test_self_test_rejects_stuck_pattern(tmp_path, monkeypatch):
    exe = tmp_path / "dslab.exe"
    exe.write_bytes(b"stub")
    raw_file = tmp_path / "capture.raw"
    raw_file.write_bytes(b"\x00" * 128)
    backend = DslabBackend(executable=str(exe))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _FakeCompleted({
            "ok": True,
            "raw_path": str(raw_file),
            "sample_rate": 100_000_000,
            "sample_count": 1024,
            "run_dir": str(tmp_path),
        }),
    )
    with pytest.raises(Exception, match="stuck"):
        backend.self_test()


def test_self_test_accepts_alternating_pattern(tmp_path, monkeypatch):
    exe = tmp_path / "dslab.exe"
    exe.write_bytes(b"stub")
    raw_file = tmp_path / "capture.raw"
    raw_file.write_bytes(b"\xAA" * 128)
    backend = DslabBackend(executable=str(exe))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _FakeCompleted({
            "ok": True,
            "raw_path": str(raw_file),
            "sample_rate": 100_000_000,
            "sample_count": 1024,
            "run_dir": str(tmp_path),
        }),
    )
    result = backend.self_test()
    assert result["ok"] is True
    assert result["sample_rate_hz"] == 100_000_000
    assert result["raw_bytes"] == 128
