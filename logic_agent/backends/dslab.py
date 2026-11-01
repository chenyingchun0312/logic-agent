from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import BackendUnavailableError, CaptureError, ValidationError
from ..models import CaptureRequest, CapturedSignal, Channel
from .base import Device

_EDGE = {"rising": "R", "falling": "F", "either": "C"}


@dataclass(frozen=True)
class _DslabDevice(Device):
    handle: int = 0


class DslabBackend:
    """DSLogic Plus capture through the local dslab CLI.

    dslab links DreamSourceLab's libsigrok4DSL. Raw samples use DSLogic's
    cross-block layout: 64 samples x enabled channels, one bit per sample.
    """

    name = "dslab"

    def __init__(self, executable: str | None = None, timeout_s: float = 120.0) -> None:
        self.executable = executable or os.environ.get("DSLAB_EXE") or self._discover()
        self.timeout_s = timeout_s

    @staticmethod
    def _discover() -> str | None:
        app = Path(__file__).resolve().parents[2]  # <root>/runtime/app in the portable bundle
        candidates = [
            # Portable bundle: runtime/app/logic_agent/backends -> runtime/bin/dslab.exe.
            app / "bin" / "dslab.exe",
            app.parent / "bin" / "dslab.exe",
            # Source checkout: dslab/build/dslab.exe.
            app / "dslab" / "build" / "dslab.exe",
            Path.cwd() / "dslab" / "build" / "dslab.exe",
        ]
        for item in candidates:
            if item.is_file():
                return str(item)
        return shutil.which("dslab")

    @property
    def available(self) -> bool:
        return bool(self.executable) and Path(self.executable).is_file()

    def _require_executable(self) -> str:
        if not self.available:
            raise BackendUnavailableError(
                "dslab.exe was not found. Build dslab and set DSLAB_EXE to its full path."
            )
        return str(self.executable)

    def list_devices(self) -> list[Device]:
        payload = self._run(["devices", "--json"])
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list):
            raise CaptureError("dslab devices returned no device array")
        devices = []
        for item in raw_devices:
            name = str(item.get("name") or "DSLogic")
            handle = int(item.get("handle") or 0)
            devices.append(
                _DslabDevice(
                    id=f"handle-{handle}" if handle else "dslogic",
                    name=name,
                    driver="dslab",
                    channels=tuple(f"D{i}" for i in range(16)),
                    serial=str(handle) if handle else None,
                    handle=handle,
                )
            )
        return devices

    def capture(self, request: CaptureRequest) -> CapturedSignal:
        executable = self._require_executable()
        devices = self.list_devices()
        if not devices:
            raise BackendUnavailableError(
                "dslab found no DSLogic device. Check USB power/data cable and run 'dslab firmware status --json'."
            )
        device = _select_device(devices, request.device)

        # dslab emits raw data blocks in probe order (ascending channel
        # index), regardless of the order in --channels. Sort both the
        # command and the mapping so D3,D0 cannot silently swap waveforms.
        requested_channels = tuple(
            sorted(request.channels, key=lambda item: int(item[1:]))
        )

        command = [
            "capture", "once",
            "--channels", ",".join(requested_channels),
            "--rate", str(request.sample_rate_hz),
            "--samples", str(request.sample_count),
            "--json",
        ]
        if request.trigger is not None:
            command += [
                "--trigger",
                f"{request.trigger.channel}={_EDGE[request.trigger.edge]}",
            ]
        payload, stderr = self._run_with_stderr(command)
        if not payload.get("ok"):
            raise CaptureError(
                "dslab capture failed",
                details={"message": payload.get("message"), "stderr": stderr[-4000:]},
            )
        raw_path = Path(str(payload.get("raw_path") or ""))
        if not raw_path.is_file():
            raise CaptureError("dslab reported success but the raw file is missing")
        raw = raw_path.read_bytes()

        enabled = int(payload.get("enabled_channels") or len(requested_channels))
        raw_bytes = int(payload.get("raw_bytes") or len(raw))
        sample_count = int(payload.get("sample_count") or 0)
        if enabled <= 0:
            raise CaptureError("dslab returned no enabled channel count")
        expected = (sample_count * enabled + 7) // 8
        if raw_bytes < expected:
            raise CaptureError(
                "dslab returned fewer raw bytes than metadata claims",
                details={"expected": expected, "raw_bytes": raw_bytes, "sample_count": sample_count},
            )

        samples = _unpack_cross(raw[:expected], sample_count, enabled)
        channels = tuple(
            Channel(id=channel, name=channel, index=int(channel[1:]))
            for channel in requested_channels
        )
        samples = {channel.id: samples[index] for index, channel in enumerate(channels)}

        warnings: list[str] = []
        if payload.get("overflow"):
            warnings.append("device reported buffer overflow")
        if payload.get("timed_out"):
            warnings.append("capture timed out")
        if request.trigger is not None and not payload.get("triggered"):
            warnings.append("trigger did not report a hit")

        native = {
            "dslab-raw": raw_path,
            "dslab-meta": raw_path.with_name("meta.json"),
        }
        vcd = payload.get("vcd_path")
        if vcd and Path(str(vcd)).is_file():
            native["dslab-vcd"] = Path(str(vcd))

        return CapturedSignal(
            backend=self.name,
            device_id=device.id,
            device_label=device.name,
            channels=channels,
            sample_rate_hz=int(payload.get("sample_rate") or request.sample_rate_hz),
            samples=samples,
            native_files=native,
            trigger_applied=request.trigger is not None,
            warnings=warnings,
        )

    def self_test(self) -> dict:
        """Run the DSLogic internal test pattern through the real sample path."""
        payload = self._run(["capture", "once", "--channels", "D0",
                             "--operation", "internal", "--samples", "4096",
                             "--json"])
        if not payload.get("ok"):
            raise CaptureError(
                "dslab internal self-test failed",
                details={"message": payload.get("message")},
            )
        raw_path = Path(str(payload.get("raw_path") or ""))
        if not raw_path.is_file():
            raise CaptureError("dslab self-test reported success but raw is missing")
        raw = raw_path.read_bytes()
        if not raw:
            raise CaptureError("dslab self-test produced no samples")
        # One enabled channel uses DSLogic's alternating test pattern; every
        # byte is 0xAA or 0x55, and the stream must not be stuck high/low.
        if any(byte not in (0xAA, 0x55) for byte in raw):
            raise CaptureError(
                "internal test pattern is not alternating (stuck or corrupt sample path)",
                details={"raw_bytes": len(raw)},
            )
        ones = sum(byte.bit_count() for byte in raw)
        if ones in (0, len(raw) * 8):
            raise CaptureError("internal test pattern is stuck", details={"raw_bytes": len(raw)})
        return {
            "ok": True,
            "mode": "internal-test-pattern",
            "sample_rate_hz": int(payload.get("sample_rate") or 0),
            "sample_count": int(payload.get("sample_count") or 0),
            "raw_bytes": len(raw),
            "run_dir": payload.get("run_dir"),
        }

    def _run(self, args: list[str]) -> dict:
        payload, _ = self._run_with_stderr(args)
        return payload

    def _run_with_stderr(self, args: list[str]) -> tuple[dict, str]:
        command = [self._require_executable(), *args]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(
                "dslab timed out",
                details={"timeout_s": self.timeout_s, "command": command},
            ) from exc
        except OSError as exc:
            raise BackendUnavailableError(f"failed to start dslab: {exc}") from exc
        text = (completed.stdout or "").strip()
        try:
            payload = json.loads(text.splitlines()[-1]) if text else {}
        except json.JSONDecodeError as exc:
            raise CaptureError(
                "dslab returned invalid JSON",
                details={"stdout": text[-4000:], "stderr": (completed.stderr or "")[-4000:]},
            ) from exc
        if completed.returncode != 0 and payload.get("ok") is not False:
            raise CaptureError(
                "dslab failed",
                details={
                    "returncode": completed.returncode,
                    "stdout": text[-4000:],
                    "stderr": (completed.stderr or "")[-4000:],
                },
            )
        return payload, completed.stderr or ""


def _select_device(devices: list[Device], requested: str | None) -> Device:
    if requested is None:
        return devices[0]
    for device in devices:
        if requested in {device.id, device.name, device.serial}:
            return device
    raise ValidationError(
        f"unknown device: {requested}",
        details={"devices": [device.to_dict() for device in devices]},
    )


def _unpack_cross(raw: bytes, sample_count: int, enabled: int) -> list[bytes]:
    if enabled <= 0:
        raise ValidationError("enabled channel count must be positive")
    samples: list[bytearray] = [bytearray(sample_count) for _ in range(enabled)]
    for sample in range(sample_count):
        frame = sample // 64
        bit = sample % 64
        byte_index = bit // 8
        mask = 1 << (bit % 8)
        for channel in range(enabled):
            offset = (frame * enabled + channel) * 8 + byte_index
            if offset >= len(raw):
                raise CaptureError(
                    "raw capture ended before the reported sample count",
                    details={"sample": sample, "channel": channel, "offset": offset, "bytes": len(raw)},
                )
            if raw[offset] & mask:
                samples[channel][sample] = 1
    return [bytes(item) for item in samples]
