from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import ValidationError


def pack_bits(samples: bytes) -> bytes:
    """Pack one-byte-per-sample 0/1 data into a bit stream (LSB first)."""
    out = bytearray((len(samples) + 7) // 8)
    for index, value in enumerate(samples):
        if value:
            out[index >> 3] |= 1 << (index & 7)
    return bytes(out)


def unpack_bits(packed: bytes, sample_count: int) -> bytes:
    if sample_count < 0:
        raise ValidationError("sample_count must be non-negative")
    out = bytearray(sample_count)
    for index in range(sample_count):
        out[index] = (packed[index >> 3] >> (index & 7)) & 1
    return bytes(out)


def edge_indices(samples: bytes) -> tuple[list[int], list[int]]:
    rising: list[int] = []
    falling: list[int] = []
    previous = samples[0] if samples else 0
    for index in range(1, len(samples)):
        value = samples[index]
        if value == previous:
            continue
        if value:
            rising.append(index)
        else:
            falling.append(index)
        previous = value
    return rising, falling


def runs(samples: bytes) -> list[tuple[int, int, int]]:
    """Return (value, start_index, length) runs."""
    if not samples:
        return []
    result: list[tuple[int, int, int]] = []
    start = 0
    value = samples[0]
    for index in range(1, len(samples)):
        if samples[index] == value:
            continue
        result.append((value, start, index - start))
        start = index
        value = samples[index]
    result.append((value, start, len(samples) - start))
    return result


@dataclass(frozen=True)
class ChannelSummary:
    channel: str
    transitions: int
    rising_edges: int
    falling_edges: int
    high_ratio: float
    estimated_frequency_hz: float | None
    min_high_s: float | None
    max_high_s: float | None
    min_low_s: float | None
    max_low_s: float | None
    first_edge_s: float | None
    last_edge_s: float | None

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "transitions": self.transitions,
            "rising_edges": self.rising_edges,
            "falling_edges": self.falling_edges,
            "high_ratio": round(self.high_ratio, 6),
            "estimated_frequency_hz": _round_optional(self.estimated_frequency_hz, 3),
            "min_high_s": _round_optional(self.min_high_s, 9),
            "max_high_s": _round_optional(self.max_high_s, 9),
            "min_low_s": _round_optional(self.min_low_s, 9),
            "max_low_s": _round_optional(self.max_low_s, 9),
            "first_edge_s": _round_optional(self.first_edge_s, 9),
            "last_edge_s": _round_optional(self.last_edge_s, 9),
        }


def summarize_channel(channel: str, samples: bytes, sample_rate_hz: int) -> ChannelSummary:
    if not samples:
        raise ValidationError("cannot summarize an empty channel")
    if sample_rate_hz <= 0:
        raise ValidationError("sample_rate_hz must be positive")

    rising, falling = edge_indices(samples)
    transition_indices = sorted(rising + falling)
    high_samples = sum(samples)
    duration_s = len(samples) / sample_rate_hz
    high_runs = [length for value, _, length in runs(samples) if value]
    low_runs = [length for value, _, length in runs(samples) if not value]

    # Ignore the partial first/last runs when estimating pulse widths.
    first_value = samples[0]
    last_value = samples[-1]
    if first_value:
        high_runs = high_runs[1:]
    else:
        low_runs = low_runs[1:]
    if high_runs and last_value:
        high_runs = high_runs[:-1]
    if low_runs and not last_value:
        low_runs = low_runs[:-1]

    frequency = None
    if len(transition_indices) >= 2:
        span = (transition_indices[-1] - transition_indices[0]) / sample_rate_hz
        if span > 0:
            full_cycles = max(1, len(rising) if len(rising) == len(falling) else len(transition_indices) // 2)
            frequency = full_cycles / span

    def seconds(values: list[int], choose_max: bool) -> float | None:
        if not values:
            return None
        return (max(values) if choose_max else min(values)) / sample_rate_hz

    return ChannelSummary(
        channel=channel,
        transitions=len(transition_indices),
        rising_edges=len(rising),
        falling_edges=len(falling),
        high_ratio=high_samples / len(samples),
        estimated_frequency_hz=frequency,
        min_high_s=seconds(high_runs, False),
        max_high_s=seconds(high_runs, True),
        min_low_s=seconds(low_runs, False),
        max_low_s=seconds(low_runs, True),
        first_edge_s=(transition_indices[0] / sample_rate_hz) if transition_indices else None,
        last_edge_s=(transition_indices[-1] / sample_rate_hz) if transition_indices else None,
    )


def _round_optional(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def measure_periodic(
    channel: str,
    samples: bytes,
    sample_rate_hz: int,
    *,
    max_cycles: int = 10_000,
) -> dict:
    """Measure a digital clock/pulse train, including stability and duty cycle."""
    if not samples:
        raise ValidationError("cannot measure an empty channel")
    if sample_rate_hz <= 0:
        raise ValidationError("sample_rate_hz must be positive")
    if max_cycles <= 0:
        raise ValidationError("max_cycles must be positive")

    rising, falling = edge_indices(samples)
    if len(rising) < 2:
        return {
            "channel": channel,
            "periodic": False,
            "reason": "fewer than two complete rising edges in this window",
            "rising_edges": len(rising),
            "falling_edges": len(falling),
        }

    rises = rising[: max_cycles + 1]
    periods = [(b - a) / sample_rate_hz for a, b in zip(rises, rises[1:])]
    falling_set = iter(falling)
    next_fall = next(falling_set, None)
    high_widths: list[float] = []
    for start, end in zip(rises, rises[1:]):
        while next_fall is not None and next_fall <= start:
            next_fall = next(falling_set, None)
        if next_fall is None or next_fall >= end:
            continue
        high_widths.append((next_fall - start) / sample_rate_hz)
        next_fall = next(falling_set, None)

    mean_period = sum(periods) / len(periods)
    variance = sum((period - mean_period) ** 2 for period in periods) / len(periods)
    stddev = math.sqrt(variance)
    mean_high = sum(high_widths) / len(high_widths) if high_widths else None
    duty = mean_high / mean_period if mean_high is not None and mean_period > 0 else None
    return {
        "channel": channel,
        "periodic": True,
        "cycles": len(periods),
        "truncated": len(rising) > len(rises),
        "period_s": round(mean_period, 12),
        "frequency_hz": round(1.0 / mean_period, 6) if mean_period > 0 else None,
        "jitter_rms_s": round(stddev, 12),
        "jitter_pp_s": round(max(periods) - min(periods), 12),
        "high_time_s": round(mean_high, 12) if mean_high is not None else None,
        "duty_cycle": round(duty, 6) if duty is not None else None,
        "min_period_s": round(min(periods), 12),
        "max_period_s": round(max(periods), 12),
    }

