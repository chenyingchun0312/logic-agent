from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .analysis import protocol_hints, select_window, window_report
from .backends import Backend, DslabBackend, MockBackend, SigrokBackend
from .decoders import DecodeRequest, DecoderRuntime, decode_capture, protocol_specs, supported_protocols
from .errors import LogicAgentError, NotFoundError, ValidationError
from .models import CaptureRequest, Limits
from .protocols import UartSettings, decode_uart
from .signal import measure_periodic, summarize_channel
from .store import CaptureStore


DEFAULT_RESOURCE_URI_TEMPLATE = "logic-agent://captures/{capture_id}"


class LogicAgentService:
    def __init__(
        self,
        *,
        data_dir: Path | str | None = None,
        backend: Backend | None = None,
        limits: Limits | None = None,
    ) -> None:
        self.limits = limits or Limits()
        self.data_dir = Path(data_dir or os.environ.get("LOGIC_AGENT_DATA_DIR") or ".logic_agent_data").resolve()
        self.store = CaptureStore(self.data_dir / "captures")
        self.backends: dict[str, Backend] = {
            "mock": MockBackend(),
            "sigrok": SigrokBackend(),
            "dslab": DslabBackend(),
        }
        self.decoder_runtime = DecoderRuntime.discover()
        if backend is not None:
            self.backends[backend.name] = backend
        self.default_backend = os.environ.get("LOGIC_AGENT_BACKEND") or self._auto_backend()

    def _auto_backend(self) -> str:
        dslab = self.backends.get("dslab")
        if getattr(dslab, "available", False):
            return "dslab"
        sigrok = self.backends.get("sigrok")
        return "sigrok" if getattr(sigrok, "available", False) else "mock"

    def _backend(self, name: str | None) -> Backend:
        selected = name or self.default_backend
        try:
            return self.backends[selected]
        except KeyError as exc:
            raise ValidationError(
                f"unknown backend: {selected}",
                details={"available_backends": sorted(self.backends)},
            ) from exc

    def capabilities(self) -> dict[str, Any]:
        return {
            "tool": "logic_agent",
            "version": "0.1.0",
            "backends": {
                name: {
                    "available": bool(getattr(backend, "available", True)),
                    "kind": "hardware" if name in {"sigrok", "dslab"} else "simulation",
                }
                for name, backend in sorted(self.backends.items())
            },
            "default_backend": self.default_backend,
            "limits": vars(self.limits),
            "protocol_decoding": {
                "runtime": (
                    {
                        "available": self.decoder_runtime.available,
                        "source": self.decoder_runtime.source,
                        "executable": self.decoder_runtime.executable,
                        "decoder_dir": (
                            str(self.decoder_runtime.decoder_dir)
                            if self.decoder_runtime.decoder_dir is not None
                            else None
                        ),
                    }
                    if self.decoder_runtime is not None
                    else {"available": False}
                ),
                "supported_protocols": supported_protocols(),
                "option_syntax": (
                    "Decoder option values are validated by type. OPTION and DECODER.OPTION "
                    "are both accepted; use the latter when stacked decoders use the same "
                    "option name. CLI options must not contain commas or colons."
                ),
                "stacked_decoders": True,
                "protocols": {
                    name: spec.describe()
                    for name, spec in sorted(protocol_specs().items())
                },
            },
            "resource_uri_template": DEFAULT_RESOURCE_URI_TEMPLATE,
            "next_steps": [
                "Call list_devices before capturing.",
                "Capture a short window, inspect summaries, then drill into another window.",
                "Use read_resource for a bounded preview; raw arrays are intentionally not returned in tool results.",
            ],
        }

    def list_devices(self, backend: str | None = None) -> dict[str, Any]:
        selected = self._backend(backend)
        try:
            devices = selected.list_devices()
        except LogicAgentError as exc:
            return {**exc.to_dict(), "backend": selected.name}
        return {
            "ok": True,
            "backend": selected.name,
            "devices": [device.to_dict() for device in devices],
        }

    def capture(self, args: dict[str, Any]) -> dict[str, Any]:
        request = CaptureRequest.from_args(args)
        self._check_limits(request)
        backend = self._backend(request.backend)
        captured = backend.capture(request)
        captured.validate()
        self._check_limits(request, captured.sample_count)
        manifest = self.store.save(captured, request)
        report = {
            "capture_id": manifest["id"],
            "backend": manifest["backend"],
            "device": manifest["device"],
            "sample_rate_hz": manifest["sample_rate_hz"],
            "sample_count": manifest["sample_count"],
            "duration_s": manifest["duration_s"],
            "channels": [
                {"id": channel.id, "summary": summarize_channel(
                    channel.id,
                    captured.samples[channel.id],
                    captured.sample_rate_hz,
                ).to_dict()}
                for channel in captured.channels
            ],
            "trigger": manifest["trigger"],
            "trigger_applied": manifest["trigger_applied"],
            "warnings": manifest["warnings"],
            "resource_uri": DEFAULT_RESOURCE_URI_TEMPLATE.format(capture_id=manifest["id"]),
        }
        report["hints"] = [
            hint
            for channel in captured.channels
            for hint in protocol_hints(
                summarize_channel(channel.id, captured.samples[channel.id], captured.sample_rate_hz).to_dict()
            )
        ]
        return {"ok": True, **report}

    def list_captures(self) -> dict[str, Any]:
        captures = self.store.list()
        return {"ok": True, "captures": captures, "count": len(captures)}

    def self_test(self, backend_name: str | None = None) -> dict[str, Any]:
        backend = self._backend(backend_name or "dslab")
        probe = getattr(backend, "self_test", None)
        if probe is None:
            raise ValidationError(
                f"backend {backend.name} has no hardware self-test",
                details={"available_backends": ["dslab"]},
            )
        result = probe()
        return {
            **result,
            "backend": backend.name,
            "note": (
                "This validates USB, firmware, FPGA, sample capture and output files. "
                "It does not validate an external probe connection or protocol."
            ),
        }

    def import_csv(self, args: dict[str, Any]) -> dict[str, Any]:
        from .importer import inspect_csv, iter_csv_samples
        from .models import Channel, CapturedSignal

        path = Path(_require_str(args, "path")).expanduser().resolve()
        if path.suffix.lower() != ".csv":
            raise ValidationError("only DSView CSV exports are supported")
        requested = args.get("channels")
        if requested is not None and (not isinstance(requested, list) or not requested):
            raise ValidationError("channels must be a non-empty array when provided")
        info = inspect_csv(path, max_scan_rows=min(self.limits.max_total_samples, 20_000))
        channels = tuple(str(item) for item in requested) if requested else info.channels
        if len(channels) > self.limits.max_channels:
            raise ValidationError(f"at most {self.limits.max_channels} channels may be imported")
        max_rows = self.limits.max_total_samples // max(1, len(channels))
        info, samples = iter_csv_samples(path, channels, max_rows=max_rows)
        total = len(next(iter(samples.values())))
        if total >= max_rows:
            raise ValidationError(
                "CSV is larger than the configured sample budget; export a narrower time range",
                details={"max_rows_per_channel": max_rows, "hint": "Use DSView cursors to select one region"},
            )
        request = CaptureRequest(
            channels=channels,
            sample_rate_hz=info.sample_rate_hz,
            duration_s=max(total, 1) / info.sample_rate_hz,
            label=(str(args["label"]).strip() or None) if args.get("label") is not None else None,
        )
        captured = CapturedSignal(
            backend="dsview-csv",
            device_id="imported-csv",
            device_label=str(path),
            channels=tuple(Channel(id=channel, name=channel, index=index) for index, channel in enumerate(channels)),
            sample_rate_hz=info.sample_rate_hz,
            samples=samples,
        )
        manifest = self.store.save(captured, request)
        return {
            "ok": True,
            "capture_id": manifest["id"],
            "source": str(path),
            "sample_rate_hz": manifest["sample_rate_hz"],
            "sample_count": manifest["sample_count"],
            "duration_s": manifest["duration_s"],
            "channels": [
                {"id": channel, "summary": summarize_channel(channel, samples[channel], info.sample_rate_hz).to_dict()}
                for channel in channels
            ],
            "resource_uri": DEFAULT_RESOURCE_URI_TEMPLATE.format(capture_id=manifest["id"]),
        }

    def measure_period(self, args: dict[str, Any]) -> dict[str, Any]:
        capture_id = _require_str(args, "capture_id")
        channel_id = _require_str(args, "channel")
        manifest = self.store.manifest(capture_id)
        samples, _ = self.store.load_channel(capture_id, channel_id)
        start_s = _float_arg(args, "start_s", 0.0)
        duration_s = _optional_float_arg(args, "duration_s")
        window = select_window(samples, manifest["sample_rate_hz"], start_s, duration_s)
        report = measure_periodic(channel_id, window.samples, manifest["sample_rate_hz"])
        return {
            "ok": True,
            "capture_id": capture_id,
            "window": {
                "start_s": round(window.offset_samples / manifest["sample_rate_hz"], 9),
                "duration_s": round(len(window.samples) / manifest["sample_rate_hz"], 9),
            },
            **report,
        }

    def analyze_window(self, args: dict[str, Any]) -> dict[str, Any]:
        capture_id = _require_str(args, "capture_id")
        channel_id = _require_str(args, "channel")
        manifest = self.store.manifest(capture_id)
        samples, _ = self.store.load_channel(capture_id, channel_id)
        start_s = _float_arg(args, "start_s", 0.0)
        duration_s = _optional_float_arg(args, "duration_s")
        max_edges = int(args.get("max_edges", 80))
        window = select_window(samples, manifest["sample_rate_hz"], start_s, duration_s)
        report = window_report(window, channel_id, manifest["sample_rate_hz"], max_edges=max_edges)
        return {"ok": True, "capture_id": capture_id, "channel": channel_id, **report, "hints": protocol_hints(report["summary"])}

    def decode_uart(self, args: dict[str, Any]) -> dict[str, Any]:
        capture_id = _require_str(args, "capture_id")
        channel_id = _require_str(args, "channel")
        manifest = self.store.manifest(capture_id)
        samples, _ = self.store.load_channel(capture_id, channel_id)
        start_s = _float_arg(args, "start_s", 0.0)
        duration_s = _optional_float_arg(args, "duration_s")
        window = select_window(samples, manifest["sample_rate_hz"], start_s, duration_s)
        settings = UartSettings.from_mapping(args.get("uart"))
        decoded, groups, warnings = decode_uart(
            window.samples,
            manifest["sample_rate_hz"],
            settings,
            max_bytes=int(args.get("max_bytes", 4096)),
        )
        return {
            "ok": True,
            "capture_id": capture_id,
            "channel": channel_id,
            "window": {
                "start_s": round(window.offset_samples / manifest["sample_rate_hz"], 9),
                "duration_s": round(len(window.samples) / manifest["sample_rate_hz"], 9),
            },
            "settings": vars(settings),
            "byte_count": len(decoded),
            "groups": [group.to_dict() for group in groups],
            "warnings": warnings,
        }

    def decode_protocol(self, args: dict[str, Any]) -> dict[str, Any]:
        capture_id = _require_str(args, "capture_id")
        protocol = _require_str(args, "protocol").lower()
        channel_map = args.get("channels")
        if not isinstance(channel_map, dict) or not channel_map:
            raise ValidationError("channels must be an object such as {\"scl\": \"D0\", \"sda\": \"D1\"}")
        normalized = {str(key): str(value) for key, value in channel_map.items()}
        options = args.get("options") or {}
        if not isinstance(options, dict):
            raise ValidationError("options must be an object")
        request = DecodeRequest(
            capture_id=capture_id,
            protocol=protocol,
            channel_map=normalized,
            options=dict(options),
            start_s=_float_arg(args, "start_s", 0.0),
            duration_s=_optional_float_arg(args, "duration_s"),
            max_events=int(args.get("max_events", 500)),
            include_bits=bool(args.get("include_bits", False)),
            include_lower_layers=bool(args.get("include_lower_layers", False)),
        )
        return decode_capture(store=self.store, runtime=self.decoder_runtime, request=request)

    def read_resource(self, capture_id: str) -> tuple[str, str]:
        manifest = self.store.manifest(capture_id)
        text = json.dumps(manifest, indent=2, ensure_ascii=False)
        if len(text) > self.limits.max_tool_chars:
            preview = {
                "id": manifest["id"],
                "created_at": manifest["created_at"],
                "label": manifest.get("label"),
                "device": manifest["device"],
                "sample_rate_hz": manifest["sample_rate_hz"],
                "sample_count": manifest["sample_count"],
                "duration_s": manifest["duration_s"],
                "channels": [
                    {key: channel[key] for key in ("id", "name", "sample_count", "sha256")}
                    for channel in manifest["channels"]
                ],
                "note": "Full manifest omitted because it exceeds max_tool_chars.",
            }
            text = json.dumps(preview, indent=2, ensure_ascii=False)
        return text, "application/json"

    def _check_limits(self, request: CaptureRequest, actual_samples: int | None = None) -> None:
        if len(request.channels) > self.limits.max_channels:
            raise ValidationError(f"at most {self.limits.max_channels} channels may be captured")
        if request.sample_rate_hz > self.limits.max_sample_rate_hz:
            raise ValidationError(f"sample_rate_hz exceeds the configured limit of {self.limits.max_sample_rate_hz}")
        if request.duration_s > self.limits.max_duration_s:
            raise ValidationError(f"duration_s exceeds the configured limit of {self.limits.max_duration_s}")
        samples = actual_samples or request.sample_count
        if samples * len(request.channels) > self.limits.max_total_samples:
            raise ValidationError(
                "capture would exceed the configured sample budget",
                details={
                    "requested_samples": samples,
                    "channels": len(request.channels),
                    "max_total_samples": self.limits.max_total_samples,
                },
            )
        if samples * len(request.channels) > self.limits.max_capture_bytes:
            raise ValidationError("capture would exceed the configured byte budget")


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} is required")
    return value.strip()


def _float_arg(args: dict[str, Any], key: str, default: float) -> float:
    value = args.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} must be a number") from exc


def _optional_float_arg(args: dict[str, Any], key: str) -> float | None:
    value = args.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} must be a number") from exc


