from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError
from .signal import edge_indices, summarize_channel


@dataclass(frozen=True)
class Window:
    offset_samples: int
    samples: bytes


def select_window(samples: bytes, sample_rate_hz: int, start_s: float, duration_s: float | None) -> Window:
    if start_s < 0:
        raise ValidationError("start_s must be non-negative")
    offset = int(round(start_s * sample_rate_hz))
    if offset >= len(samples):
        raise ValidationError("start_s is outside the capture", details={"capture_duration_s": len(samples) / sample_rate_hz})
    if duration_s is None:
        stop = len(samples)
    else:
        if duration_s <= 0:
            raise ValidationError("duration_s must be positive")
        stop = min(len(samples), offset + max(1, int(round(duration_s * sample_rate_hz))))
    return Window(offset_samples=offset, samples=samples[offset:stop])


def window_report(
    window: Window,
    channel: str,
    sample_rate_hz: int,
    *,
    max_edges: int = 80,
) -> dict:
    if max_edges <= 0:
        raise ValidationError("max_edges must be positive")
    summary = summarize_channel(channel, window.samples, sample_rate_hz).to_dict()
    rising, falling = edge_indices(window.samples)
    edges = sorted([(index, "rising") for index in rising] + [(index, "falling") for index in falling])
    returned = edges[:max_edges]
    return {
        "offset_s": round(window.offset_samples / sample_rate_hz, 9),
        "duration_s": round(len(window.samples) / sample_rate_hz, 9),
        "sample_count": len(window.samples),
        "summary": summary,
        "edges": [
            {
                "index": window.offset_samples + index,
                "time_s": round((window.offset_samples + index) / sample_rate_hz, 9),
                "type": kind,
            }
            for index, kind in returned
        ],
        "edges_truncated": len(edges) > len(returned),
        "total_edges": len(edges),
    }


def protocol_hints(summary: dict) -> list[str]:
    """Cheap, deterministic hints that steer the agent toward a decoder."""
    hints: list[str] = []
    frequency = summary.get("estimated_frequency_hz")
    transitions = summary.get("transitions", 0)
    high_ratio = summary.get("high_ratio", 0)
    if transitions == 0:
        hints.append("No edges in this window: line is idle, mis-probed, or the window is too narrow.")
        return hints
    if 0.2 < high_ratio < 0.8 and frequency:
        hints.append(
            f"Periodic square-ish activity near {frequency:g} Hz; check it as a clock/PWM before decoding serial data."
        )
    if 0.02 < high_ratio < 0.98 and transitions >= 8:
        hints.append("Irregular edges may be asynchronous serial (UART) or a framed bus (I2C/SPI).")
    min_high = summary.get("min_high_s")
    min_low = summary.get("min_low_s")
    if min_high and min_low:
        shortest = min(min_high, min_low)
        if shortest > 0:
            estimated_baud = round(1 / shortest)
            hints.append(f"Shortest pulse is {shortest:g} s; if this is UART, try baud near {estimated_baud}.")
    return hints

