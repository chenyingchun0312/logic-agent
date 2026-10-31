from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TextIO

from .errors import ValidationError


@dataclass(frozen=True)
class CsvInfo:
    sample_rate_hz: int
    channels: tuple[str, ...]
    row_count: int
    time_column: str | None
    delimiter: str


def inspect_csv(path: Path, *, max_scan_rows: int = 20_000) -> CsvInfo:
    """Read DSView CSV headers without loading the whole file."""
    if not path.exists():
        raise ValidationError(f"CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        delimiter = _sniff_delimiter(handle)
        rows = csv.reader(handle, delimiter=delimiter)
        header = _read_header(rows)
        time_column = next(cell for cell in header if cell.lower().startswith("time"))
        channel_columns = tuple(cell for cell in header if cell and cell.lower() != time_column.lower())
        if not channel_columns:
            raise ValidationError("CSV has no channel columns")

        count = 0
        previous_time: float | None = None
        deltas: list[float] = []
        for row in rows:
            if not row:
                continue
            try:
                value = float(row[header.index(time_column)])
            except (ValueError, IndexError):
                continue
            if previous_time is not None:
                delta = value - previous_time
                if delta > 0:
                    deltas.append(delta)
            previous_time = value
            count += 1
            if count >= max_scan_rows or len(deltas) >= 4096:
                break

    if count == 0 or not deltas:
        raise ValidationError("CSV has no usable time samples")
    median_dt = sorted(deltas)[len(deltas) // 2]
    if median_dt <= 0 or math.isinf(median_dt):
        raise ValidationError("CSV time steps are invalid")
    return CsvInfo(
        sample_rate_hz=max(1, round(1.0 / median_dt)),
        channels=channel_columns,
        row_count=count,
        time_column=time_column,
        delimiter=delimiter,
    )


def iter_csv_samples(
    path: Path,
    channels: Iterable[str],
    *,
    max_rows: int | None = None,
) -> tuple[CsvInfo, dict[str, bytes]]:
    """Load selected channels as 0/1 bytes, with a hard row cap enforced by caller."""
    info = inspect_csv(path)
    wanted = tuple(channels)
    missing = [channel for channel in wanted if channel not in info.channels]
    if missing:
        raise ValidationError(
            "requested channels are not present in this CSV",
            details={"missing": missing, "available": list(info.channels)},
        )
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = csv.reader(handle, delimiter=info.delimiter)
        header = _read_header(rows)
        indices = {channel: header.index(channel) for channel in wanted}
        time_index = header.index(info.time_column)
        values: dict[str, bytearray] = {channel: bytearray() for channel in wanted}
        previous_time: float | None = None
        for row in rows:
            if not row:
                continue
            if max_rows is not None and len(next(iter(values.values()))) >= max_rows:
                break
            try:
                current_time = float(row[time_index])
            except (ValueError, IndexError):
                continue
            if previous_time is not None and current_time < previous_time:
                raise ValidationError("CSV time column is not monotonic")
            previous_time = current_time
            for channel in wanted:
                try:
                    value = float(row[indices[channel]])
                except (ValueError, IndexError) as exc:
                    raise ValidationError(f"invalid value for channel {channel} in CSV") from exc
                values[channel].append(1 if value != 0 else 0)
    return info, {channel: bytes(data) for channel, data in values.items()}


def _sniff_delimiter(handle: TextIO) -> str:
    sample = handle.read(8192)
    handle.seek(0)
    return "," if sample.count(",") >= sample.count(";") else ";"


def _read_header(rows: Iterable[list[str]]) -> list[str]:
    """Skip DSView's leading ';' metadata lines and return the real column row."""
    for row in rows:
        if not row:
            continue
        first = row[0].strip()
        if first.startswith(";") or first.startswith("#"):
            continue
        header = [cell.strip().lstrip("\ufeff") for cell in row]
        if any(cell.lower().startswith("time") for cell in header):
            return header
    raise ValidationError("CSV has no time header row")

