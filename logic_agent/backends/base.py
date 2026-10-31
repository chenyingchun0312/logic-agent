from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import CaptureRequest, CapturedSignal


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    driver: str
    channels: tuple[str, ...]
    serial: str | None = None
    connected: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "driver": self.driver,
            "channels": list(self.channels),
            "serial": self.serial,
            "connected": self.connected,
        }


class Backend(Protocol):
    name: str

    def list_devices(self) -> list[Device]:
        ...

    def capture(self, request: CaptureRequest) -> CapturedSignal:
        ...

