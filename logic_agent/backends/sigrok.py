from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import BackendUnavailableError, CaptureError, ValidationError
from ..models import CaptureRequest, CapturedSignal, Channel
from ..signal import unpack_bits
from .base import Device


@dataclass(frozen=True)
class _SigrokDevice(Device):
    selector: str = ""


class SigrokBackend:
    """Capture digital channels through sigrok-cli.

    sigrok covers cheap FX2-based analyzers, DSLogic, and many vendor devices.
    Raw sigrok binary output uses one byte per digital channel: bit 0 = D0.
    """

    name = "sigrok"

    def __init__(self, executable: str | None = None, timeout_s: float = 90.0) -> None:
        self.executable = executable or os.environ.get("SIGROK_CLI") or shutil.which("sigrok-cli")
        self.timeout_s = timeout_s
        self._trigger_support: tuple[str, bool] | None = None

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def _require_executable(self) -> str:
        if not self.executable:
            raise BackendUnavailableError(
                "sigrok-cli was not found. Install sigrok-cli/PulseView and put sigrok-cli on PATH, "
                "or set SIGROK_CLI to its full path."
            )
        return self.executable

    def list_devices(self) -> list[Device]:
        executable = self._require_executable()
        completed = self._run([executable, "--scan"])
        devices: list[Device] = []
        for index, raw_line in enumerate(completed.stdout.splitlines()):
            line = raw_line.strip()
            if not line or line.lower().startswith("the following devices"):
                continue
            if line.startswith("- "):
                line = line[2:].strip()
            fields = _parse_scan_line(line)
            if not fields:
                continue
            name = fields.get("name") or fields.get("device") or fields.get("driver") or f"sigrok device {index}"
            driver = fields.get("driver") or "sigrok"
            connection = fields.get("conn") or fields.get("connection")
            selector = f"{driver}:{connection}" if connection and connection not in {"conn=?", "?"} else driver
            serial = fields.get("serial") or connection
            devices.append(
                _SigrokDevice(
                    id=selector,
                    name=name,
                    driver=driver,
                    channels=tuple(_channel_ids(fields.get("channels", ""))),
                    serial=serial,
                    selector=selector,
                )
            )
        return devices

    def capture(self, request: CaptureRequest) -> CapturedSignal:
        executable = self._require_executable()
        devices = self.list_devices()
        if not devices:
            raise BackendUnavailableError("sigrok-cli found no connected logic analyzers")

        device = _select_device(devices, request.device)
        for channel in request.channels:
            if channel not in device.channels:
                raise ValidationError(
                    f"channel {channel} is not exposed by {device.name}",
                    details={"available_channels": list(device.channels)},
                )
        if request.trigger is not None and request.trigger.channel not in request.channels:
            raise ValidationError("trigger.channel must be one of the captured channels")

        sample_count = request.sample_count
        device_channels = list(device.channels)
        with tempfile.TemporaryDirectory(prefix="logic-agent-sigrok-") as temp_dir:
            raw_path = Path(temp_dir) / "capture.bin"
            channel_arg = ",".join(request.channels)
            command = [
                executable,
                "--driver", getattr(device, "selector", device.driver),
                "--config", f"samplerate={request.sample_rate_hz}",
                "--channels", channel_arg,
                "--samples", str(sample_count),
                "--output-format", "binary",
                "--output-file", str(raw_path),
            ]
            if request.trigger is not None:
                if self._supports_trigger(executable):
                    command[1:1] = [
                        "--trigger",
                        f"{request.trigger.channel}={_trigger_letter(request.trigger.edge)}",
                    ]
            self._run(command)
            if not raw_path.exists():
                raise CaptureError("sigrok-cli completed without producing a capture file")
            raw = raw_path.read_bytes()

        unitsize, layout = _infer_binary_layout(raw, sample_count, device_channels)
        expected_bytes = sample_count * unitsize
        if len(raw) < expected_bytes:
            raise CaptureError(
                "sigrok-cli returned fewer samples than requested",
                details={"expected": expected_bytes, "received": len(raw)},
            )
        raw = raw[:expected_bytes]

        samples = _unpack_channels(raw, sample_count, unitsize, layout, device_channels, request.channels)
        warnings: list[str] = []
        trigger_applied = request.trigger is not None and self._supports_trigger(executable)
        if request.trigger is not None and not trigger_applied:
            warnings.append("trigger was requested but this sigrok-cli build has no --trigger support")

        return CapturedSignal(
            backend=self.name,
            device_id=device.id,
            device_label=device.name,
            channels=tuple(Channel(id=channel, name=channel, index=index) for index, channel in enumerate(request.channels)),
            sample_rate_hz=request.sample_rate_hz,
            samples=samples,
            trigger_applied=trigger_applied,
            warnings=warnings,
        )

    def _supports_trigger(self, executable: str) -> bool:
        if self._trigger_support is not None and self._trigger_support[0] == executable:
            return self._trigger_support[1]
        completed = self._run([executable, "--help"])
        supported = "--trigger" in completed.stdout
        self._trigger_support = (executable, supported)
        return supported

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
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
                "sigrok-cli timed out",
                details={"timeout_s": self.timeout_s, "command": " ".join(command)},
            ) from exc
        except OSError as exc:
            raise BackendUnavailableError(f"failed to start sigrok-cli: {exc}") from exc
        if completed.returncode != 0:
            raise CaptureError(
                "sigrok-cli failed",
                details={
                    "returncode": completed.returncode,
                    "stderr": completed.stderr.strip()[-4000:],
                },
            )
        return completed


def _parse_scan_line(record: str) -> dict[str, str]:
    """Parse both modern and older sigrok-cli --scan lines.

    Modern: fx2lafw:conn=1.2 -- Saleae Logic 8 with 8 channels: D0 D1 ... D7
    Older:  driver: fx2lafw ... channels: D0 D1 ...
    """
    match = re.match(
        r"^(?P<driver>[A-Za-z0-9_.-]+):(?P<conn>\S+)\s+--\s+(?P<name>.+?)\s+with\s+\d+\s+channels?:\s*(?P<channels>.+)$",
        record,
    )
    if match:
        return {key: value.strip() for key, value in match.groupdict().items()}
    fields: dict[str, str] = {}
    for key, value in re.findall(r"(\w+):\s*([^:]+?)(?=\s+\w+:\s|$)", record):
        fields[key.lower()] = value.strip()
    if "driver" not in fields and record:
        fields["driver"] = record.split(":", 1)[0].strip()
    return fields


def _channel_ids(value: str) -> list[str]:
    return [item for item in re.split(r"[\s,]+", value.strip()) if item]


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


def _trigger_letter(edge: str) -> str:
    return {"rising": "r", "falling": "f", "either": "e"}[edge]


def _infer_binary_layout(raw: bytes, sample_count: int, device_channels: list[str]) -> tuple[int, str]:
    """Infer unitsize when sigrok emits raw device-layout or captured-channel layout."""
    max_unitsize = max(1, (len(device_channels) + 7) // 8)
    for unitsize in range(max_unitsize, 0, -1):
        if len(raw) == sample_count * unitsize:
            return unitsize, "device"
    return 1, "captured"


def _unpack_channels(
    raw: bytes,
    sample_count: int,
    unitsize: int,
    layout: str,
    device_channels: list[str],
    requested: tuple[str, ...],
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for channel in requested:
        if layout == "device":
            bit = device_channels.index(channel)
        else:
            bit = list(requested).index(channel)
        byte_index = bit // 8
        bit_mask = 1 << (bit % 8)
        result[channel] = bytes(
            (raw[sample * unitsize + byte_index] & bit_mask) >> (bit % 8)
            for sample in range(sample_count)
        )
    return result

