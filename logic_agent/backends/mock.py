from __future__ import annotations

import math

from ..errors import ValidationError
from ..models import CaptureRequest, CapturedSignal, Channel
from .base import Device


class MockBackend:
    """Deterministic signals for exercising the agent toolchain without hardware.

    D0: UART TX, 8N1, carrying "PING 0x2A\\n" at 115200 baud.
    D1: 1 kHz 25% duty-cycle PWM.
    D2: 100 kHz clock burst, useful for testing frequency heuristics.
    """

    name = "mock"

    def list_devices(self) -> list[Device]:
        return [
            Device(
                id="mock-0",
                name="Mock Logic Analyzer (deterministic)",
                driver="mock",
                channels=("D0", "D1", "D2", "D3"),
            )
        ]

    def capture(self, request: CaptureRequest) -> CapturedSignal:
        if request.device not in {None, "mock-0"}:
            raise ValidationError(f"unknown mock device: {request.device}")
        if request.sample_rate_hz < 1_000_000:
            raise ValidationError("mock backend needs sample_rate_hz >= 1,000,000 for clean UART decoding")

        sample_count = request.sample_count
        generators = {
            "D0": self._uart,
            "D1": self._pwm,
            "D2": self._clock_burst,
            "D3": lambda count, rate: bytes([0]) * count,
        }
        unknown = [channel for channel in request.channels if channel not in generators]
        if unknown:
            raise ValidationError("mock backend has no signal for these channels", details={"channels": unknown})

        samples = {
            channel: bytes(generators[channel](sample_count, request.sample_rate_hz))
            for channel in request.channels
        }
        warnings: list[str] = []
        trigger_applied = False
        if request.trigger is not None:
            if request.trigger.channel not in samples:
                raise ValidationError("trigger.channel must be one of the captured channels")
            trigger_applied = True
            if request.trigger.edge == "falling":
                warnings.append("mock backend is free-running; trigger edge is reported but not aligned")

        return CapturedSignal(
            backend=self.name,
            device_id="mock-0",
            device_label="Mock Logic Analyzer (deterministic)",
            channels=tuple(Channel(id=channel, name=channel, index=index) for index, channel in enumerate(request.channels)),
            sample_rate_hz=request.sample_rate_hz,
            samples=samples,
            trigger_applied=trigger_applied,
            warnings=warnings,
        )

    def _uart(self, count: int, rate: int) -> list[int]:
        bits_per_second = 115_200
        samples_per_bit = rate / bits_per_second
        payload = b"PING 0x2A\n"
        result = [1] * count
        cursor = float(int(rate * 0.002))
        for byte in payload:
            frame = [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]
            for bit in frame:
                start = int(cursor)
                end = min(count, round(cursor + samples_per_bit))
                if start >= count:
                    return result
                for index in range(start, end):
                    result[index] = bit
                cursor += samples_per_bit
            cursor += samples_per_bit  # idle between bytes
        return result

    def _pwm(self, count: int, rate: int) -> list[int]:
        period = rate / 1_000
        high = period * 0.25
        return [1 if (index % period) < high else 0 for index in range(count)]

    def _clock_burst(self, count: int, rate: int) -> list[int]:
        period = rate / 100_000
        half = period / 2
        start = int(rate * 0.01)
        stop = int(rate * 0.02)
        return [1 if start <= index < stop and (index % period) < half else 0 for index in range(count)]

