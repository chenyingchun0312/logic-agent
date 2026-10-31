from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError


@dataclass(frozen=True)
class UartSettings:
    baud: int = 115_200
    data_bits: int = 8
    parity: str = "none"
    stop_bits: float = 1.0
    idle_high: bool = True
    inverted: bool = False
    msb_first: bool = False

    @classmethod
    def from_mapping(cls, value: dict | None) -> "UartSettings":
        value = value or {}
        try:
            settings = cls(
                baud=int(value.get("baud", 115_200)),
                data_bits=int(value.get("data_bits", 8)),
                parity=str(value.get("parity", "none")).lower(),
                stop_bits=float(value.get("stop_bits", 1.0)),
                idle_high=bool(value.get("idle_high", True)),
                inverted=bool(value.get("inverted", False)),
                msb_first=bool(value.get("msb_first", False)),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid UART settings") from exc
        settings.validate()
        return settings

    def validate(self) -> None:
        if not 50 <= self.baud <= 20_000_000:
            raise ValidationError("uart.baud must be between 50 and 20,000,000")
        if self.data_bits not in {5, 6, 7, 8}:
            raise ValidationError("uart.data_bits must be 5, 6, 7, or 8")
        if self.parity not in {"none", "even", "odd"}:
            raise ValidationError("uart.parity must be none, even, or odd")
        if self.stop_bits not in {1.0, 1.5, 2.0}:
            raise ValidationError("uart.stop_bits must be 1, 1.5, or 2")


@dataclass(frozen=True)
class DecodedByte:
    start_s: float
    end_s: float
    value: int
    parity_ok: bool | None
    stop_ok: bool

    def to_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 9),
            "end_s": round(self.end_s, 9),
            "value": self.value,
            "parity_ok": self.parity_ok,
            "stop_ok": self.stop_ok,
        }


@dataclass(frozen=True)
class UartFrameGroup:
    start_s: float
    end_s: float
    data: bytes
    text: str
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 9),
            "end_s": round(self.end_s, 9),
            "data_hex": self.data.hex(" "),
            "text": self.text,
            "errors": list(self.errors),
        }


def decode_uart(
    samples: bytes,
    sample_rate_hz: int,
    settings: UartSettings,
    *,
    max_bytes: int = 4096,
    group_gap_s: float = 0.005,
) -> tuple[list[DecodedByte], list[UartFrameGroup], list[str]]:
    """Decode 8N1/8E1/8O1 UART from one-bit-per-sample data.

    Returns decoded bytes, text groups, and non-fatal warnings. A dominant idle
    level that disagrees with settings is a hard validation error because every
    subsequent bit boundary would be wrong.
    """
    if not samples:
        raise ValidationError("cannot decode UART from empty samples")
    if sample_rate_hz <= 0:
        raise ValidationError("sample_rate_hz must be positive")
    settings.validate()
    if sample_rate_hz < settings.baud * 4:
        raise ValidationError("sample_rate_hz is too low for reliable UART decoding; use at least 4x baud")
    if max_bytes <= 0:
        raise ValidationError("max_bytes must be positive")

    idle = 1 if settings.idle_high else 0
    observed_idle_ratio = sum(1 for value in samples if value == idle) / len(samples)
    inverted_idle_ratio = 1 - observed_idle_ratio
    if inverted_idle_ratio > observed_idle_ratio and not settings.inverted:
        raise ValidationError(
            "the line idles at the opposite level; set uart.inverted or uart.idle_high to match the probe",
            details={
                "configured_idle_level": idle,
                "configured_idle_ratio": round(observed_idle_ratio, 4),
                "opposite_level_ratio": round(inverted_idle_ratio, 4),
            },
        )
    logical = bytes(1 - value if settings.inverted else value for value in samples)
    idle = 1 if settings.idle_high else 0

    spb = sample_rate_hz / settings.baud
    start_samples = _edge_positions(logical, 1 - idle)
    decoded: list[DecodedByte] = []
    warnings: list[str] = []
    errors_seen = 0
    index = 0
    while index < len(start_samples) and len(decoded) < max_bytes:
        start = start_samples[index]
        begin = start + spb / 2
        end = start + (1 + settings.data_bits + (1 if settings.parity != "none" else 0) + settings.stop_bits) * spb
        if end >= len(samples):
            break
        if _sample_at(logical, begin) == idle:
            index += 1
            continue

        value = 0
        for bit in range(settings.data_bits):
            level = _majority(logical, begin + (bit + 1) * spb, spb * 0.25)
            if level:
                value |= 1 << (settings.data_bits - 1 - bit if settings.msb_first else bit)
        cursor = 1 + settings.data_bits
        parity_ok = None
        if settings.parity != "none":
            parity_bit = _majority(logical, begin + cursor * spb, spb * 0.25)
            ones = value.bit_count()
            expected = ones % 2 if settings.parity == "even" else 1 - (ones % 2)
            parity_ok = parity_bit == expected
            if not parity_ok:
                errors_seen += 1
            cursor += 1
        stop_ok = _majority(logical, begin + (cursor + settings.stop_bits / 2 - 0.5) * spb, spb * 0.25) == idle
        if not stop_ok:
            errors_seen += 1
        decoded.append(
            DecodedByte(
                start_s=start / sample_rate_hz,
                end_s=end / sample_rate_hz,
                value=value,
                parity_ok=parity_ok,
                stop_ok=stop_ok,
            )
        )
        index = _first_start_after(start_samples, index, int(end - spb / 2))

    if len(decoded) >= max_bytes:
        warnings.append(f"decode truncated at max_bytes={max_bytes}")
    if errors_seen:
        warnings.append(f"{errors_seen} framing/parity errors detected; inspect raw bytes before trusting text")
    groups = _group_bytes(decoded, group_gap_s)
    return decoded, groups, warnings


def _group_bytes(decoded: list[DecodedByte], gap_s: float) -> list[UartFrameGroup]:
    groups: list[UartFrameGroup] = []
    current: list[DecodedByte] = []
    for item in decoded:
        if current and item.start_s - current[-1].end_s > gap_s:
            groups.append(_make_group(current))
            current = []
        current.append(item)
    if current:
        groups.append(_make_group(current))
    return groups


def _make_group(items: list[DecodedByte]) -> UartFrameGroup:
    data = bytes(item.value for item in items)
    errors: list[str] = []
    if any(item.parity_ok is False for item in items):
        errors.append("parity")
    if any(not item.stop_ok for item in items):
        errors.append("framing")
    return UartFrameGroup(
        start_s=items[0].start_s,
        end_s=items[-1].end_s,
        data=data,
        text=data.decode("ascii", errors="backslashreplace"),
        errors=tuple(errors),
    )


def _edge_positions(samples: bytes, level: int) -> list[int]:
    positions: list[int] = []
    previous = samples[0]
    for index in range(1, len(samples)):
        if samples[index] == level and previous != level:
            positions.append(index)
        previous = samples[index]
    return positions


def _sample_at(samples: bytes, position: float) -> int:
    index = int(position)
    if index < 0 or index >= len(samples):
        raise ValidationError("UART decode ran past the end of the capture")
    return samples[index]


def _majority(samples: bytes, center: float, width: float) -> int:
    start = max(0, int(center - width / 2))
    stop = min(len(samples), max(start + 1, int(center + width / 2)))
    chunk = samples[start:stop]
    return 1 if sum(chunk) * 2 >= len(chunk) else 0


def _first_start_after(positions: list[int], current: int, threshold: int) -> int:
    index = current + 1
    while index < len(positions) and positions[index] <= threshold:
        index += 1
    return index

