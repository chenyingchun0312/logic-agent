from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ValidationError
from .models import CaptureRequest, CapturedSignal
from .signal import pack_bits, unpack_bits


_CAPTURE_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


class CaptureStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, captured: CapturedSignal, request: CaptureRequest) -> dict[str, Any]:
        captured.validate()
        capture_id = _new_capture_id()
        target = self.root / capture_id
        staging = self.root / f".{capture_id}.tmp"
        staging.mkdir(parents=False, exist_ok=False)

        channel_records: list[dict[str, Any]] = []
        try:
            for channel in captured.channels:
                packed = pack_bits(captured.samples[channel.id])
                filename = f"channel-{_safe_channel_id(channel.id)}.bits"
                path = staging / filename
                path.write_bytes(packed)
                channel_records.append(
                    {
                        **channel.to_dict(),
                        "file": filename,
                        "sample_count": len(captured.samples[channel.id]),
                        "bytes": len(packed),
                        "sha256": hashlib.sha256(packed).hexdigest(),
                    }
                )

            native_records: list[dict[str, Any]] = []
            for name, source in captured.native_files.items():
                if not source.exists():
                    continue
                filename = f"native-{_safe_channel_id(name)}{source.suffix or '.bin'}"
                destination = staging / filename
                destination.write_bytes(source.read_bytes())
                native_records.append(
                    {
                        "name": name,
                        "file": filename,
                        "bytes": destination.stat().st_size,
                        "sha256": _sha256_file(destination),
                    }
                )

            manifest = {
                "schema_version": "1.0",
                "id": capture_id,
                "created_at": _utc_now_iso(),
                "label": request.label,
                "backend": captured.backend,
                "device": {"id": captured.device_id, "name": captured.device_label},
                "sample_rate_hz": captured.sample_rate_hz,
                "sample_count": captured.sample_count,
                "duration_s": round(captured.sample_count / captured.sample_rate_hz, 9),
                "trigger": (
                    {"channel": request.trigger.channel, "edge": request.trigger.edge}
                    if request.trigger is not None
                    else None
                ),
                "trigger_applied": captured.trigger_applied,
                "warnings": list(captured.warnings),
                "channels": channel_records,
                "native_files": native_records,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(staging, target)
        except Exception:
            _remove_tree(staging)
            raise
        return manifest

    def list(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not path.is_dir() or not _CAPTURE_ID.match(path.name):
                continue
            manifest = self._read_manifest(path)
            summaries.append(
                {
                    "id": manifest["id"],
                    "created_at": manifest["created_at"],
                    "label": manifest.get("label"),
                    "backend": manifest["backend"],
                    "device": manifest["device"],
                    "sample_rate_hz": manifest["sample_rate_hz"],
                    "duration_s": manifest["duration_s"],
                    "channels": [channel["id"] for channel in manifest["channels"]],
                    "warnings": manifest.get("warnings", []),
                }
            )
        return summaries

    def manifest(self, capture_id: str) -> dict[str, Any]:
        return self._read_manifest(self._capture_dir(capture_id))

    def load_channel(self, capture_id: str, channel_id: str) -> tuple[bytes, dict[str, Any]]:
        manifest = self.manifest(capture_id)
        channel = next(
            (item for item in manifest["channels"] if item["id"] == channel_id),
            None,
        )
        if channel is None:
            raise NotFoundError(
                f"capture {capture_id} has no channel {channel_id}",
                details={"available_channels": [item["id"] for item in manifest["channels"]]},
            )
        directory = self._capture_dir(capture_id)
        path = (directory / channel["file"]).resolve()
        if directory not in path.parents:
            raise ValidationError("invalid channel path in manifest")
        packed = path.read_bytes()
        if hashlib.sha256(packed).hexdigest() != channel["sha256"]:
            raise ValidationError("capture data failed its integrity check", details={"capture_id": capture_id})
        samples = unpack_bits(packed, int(channel["sample_count"]))
        return samples, manifest

    def artifact_path(self, capture_id: str) -> Path:
        return self._capture_dir(capture_id)

    def _capture_dir(self, capture_id: str) -> Path:
        if not _CAPTURE_ID.match(capture_id):
            raise ValidationError("capture_id has an invalid format")
        path = (self.root / capture_id).resolve()
        if path.parent != self.root or not path.is_dir():
            raise NotFoundError(f"capture not found: {capture_id}")
        return path

    def _read_manifest(self, directory: Path) -> dict[str, Any]:
        path = directory / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise NotFoundError(f"capture manifest not found: {directory.name}") from exc
        except json.JSONDecodeError as exc:
            raise ValidationError(f"capture manifest is corrupt: {directory.name}") from exc
        if manifest.get("id") != directory.name:
            raise ValidationError(f"capture manifest id mismatch: {directory.name}")
        return manifest


def _new_capture_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_channel_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not cleaned:
        raise ValidationError("channel id cannot be represented as a filename")
    return cleaned


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    path.rmdir()

