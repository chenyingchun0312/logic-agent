from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .errors import ValidationError


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    index: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "index": self.index}


@dataclass(frozen=True)
class Trigger:
    channel: str
    edge: str = "rising"

    def validate(self) -> None:
        if self.edge not in {"rising", "falling", "either"}:
            raise ValidationError("edge must be one of: rising, falling, either")


@dataclass(frozen=True)
class CaptureRequest:
    channels: tuple[str, ...]
    sample_rate_hz: int
    duration_s: float
    backend: str | None = None
    device: str | None = None
    trigger: Trigger | None = None
    label: str | None = None

    @classmethod
    def from_args(cls, args: Mapping[str, Any]) -> "CaptureRequest":
        raw_channels = args.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            raise ValidationError("channels must be a non-empty array, for example ['D0', 'D1']")
        channels = tuple(str(channel).strip() for channel in raw_channels if str(channel).strip())
        if not channels:
            raise ValidationError("channels must contain at least one channel id")

        try:
            sample_rate_hz = int(args.get("sample_rate_hz"))
            duration_s = float(args.get("duration_s"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("sample_rate_hz and duration_s must be numbers") from exc

        raw_trigger = args.get("trigger")
        trigger = None
        if raw_trigger is not None:
            if not isinstance(raw_trigger, Mapping):
                raise ValidationError("trigger must be an object")
            trigger = Trigger(
                channel=str(raw_trigger.get("channel", "")),
                edge=str(raw_trigger.get("edge", "rising")),
            )
            if not trigger.channel:
                raise ValidationError("trigger.channel is required")
            trigger.validate()

        label = args.get("label")
        if label is not None:
            label = str(label).strip()
            if len(label) > 120:
                raise ValidationError("label must be at most 120 characters")

        request = cls(
            channels=channels,
            sample_rate_hz=sample_rate_hz,
            duration_s=duration_s,
            backend=_optional_str(args.get("backend")),
            device=_optional_str(args.get("device")),
            trigger=trigger,
            label=label or None,
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValidationError("sample_rate_hz must be positive")
        if self.duration_s <= 0:
            raise ValidationError("duration_s must be positive")
        if len(set(self.channels)) != len(self.channels):
            raise ValidationError("channels must not contain duplicates")

    @property
    def sample_count(self) -> int:
        return max(1, round(self.sample_rate_hz * self.duration_s))


@dataclass
class CapturedSignal:
    backend: str
    device_id: str
    device_label: str
    channels: tuple[Channel, ...]
    sample_rate_hz: int
    samples: dict[str, bytes]
    native_files: dict[str, Path] = field(default_factory=dict)
    trigger_applied: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def sample_count(self) -> int:
        if not self.samples:
            return 0
        return len(next(iter(self.samples.values())))

    def validate(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValidationError("backend returned a non-positive sample rate")
        if not self.channels:
            raise ValidationError("backend returned no channels")
        lengths = {channel.id: len(self.samples.get(channel.id, b"")) for channel in self.channels}
        if any(length == 0 for length in lengths.values()):
            raise ValidationError("backend returned an empty channel", details={"lengths": lengths})
        if len(set(lengths.values())) != 1:
            raise ValidationError("backend returned channels with different lengths", details={"lengths": lengths})


@dataclass(frozen=True)
class Limits:
    max_channels: int = 16
    max_sample_rate_hz: int = 50_000_000
    max_duration_s: float = 60.0
    max_total_samples: int = 5_000_000
    max_capture_bytes: int = 100_000_000
    max_tool_chars: int = 64_000
    max_resource_bytes: int = 2_000_000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

