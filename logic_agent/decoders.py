from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import BackendUnavailableError, CaptureError, ValidationError
from .signal import unpack_bits


@dataclass(frozen=True)
class OptionSpec:
    kind: str
    values: tuple[Any, ...] = ()
    minimum: float | int | None = None
    maximum: float | int | None = None
    default: Any = None

    def describe(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.kind}
        if self.values:
            result["values"] = list(self.values)
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.default is not None:
            result["default"] = self.default
        return result


@dataclass(frozen=True)
class DecoderLayer:
    decoder: str
    channels: dict[str, bool] = field(default_factory=dict)
    options: dict[str, OptionSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class ProtocolSpec:
    name: str
    layers: tuple[DecoderLayer, ...]
    options: dict[str, OptionSpec]
    option_layers: dict[str, int]
    requires_any: tuple[str, ...] = ()
    description: str = ""
    verification: str = "contract"

    @property
    def decoder(self) -> str:
        return self.layers[-1].decoder

    @property
    def channels(self) -> dict[str, bool]:
        return self.layers[0].channels

    def resolve_option(self, key: str) -> tuple[int, str, OptionSpec]:
        if key in self.options:
            return self.option_layers[key], key, self.options[key]
        if "." in key:
            decoder, option = key.split(".", 1)
            for index, layer in enumerate(self.layers):
                if layer.decoder == decoder and option in layer.options:
                    return index, option, layer.options[option]
        raise KeyError(key)

    def describe(self) -> dict[str, Any]:
        return {
            "decoder": self.decoder,
            "description": self.description,
            "verification": self.verification,
            "layers": [
                {
                    "decoder": layer.decoder,
                    "channels": {
                        name: "required" if required else "optional"
                        for name, required in layer.channels.items()
                    },
                    "options": sorted(layer.options),
                }
                for layer in self.layers
            ],
            "channels": {
                name: "required" if required else "optional"
                for name, required in self.channels.items()
            },
            "requires_one_of": list(self.requires_any),
            "options": {
                key: {"decoder": self.layers[self.option_layers[key]].decoder, **spec.describe()}
                for key, spec in sorted(self.options.items())
            },
        }


def _enum(*values: Any, default: Any = None) -> OptionSpec:
    return OptionSpec("enum", tuple(values), default=default)


def _integer(minimum: int | None = None, maximum: int | None = None, default: int | None = None) -> OptionSpec:
    return OptionSpec("integer", minimum=minimum, maximum=maximum, default=default)


def _number(minimum: float | None = None, maximum: float | None = None, default: float | None = None) -> OptionSpec:
    return OptionSpec("number", minimum=minimum, maximum=maximum, default=default)


def _protocol(
    name: str,
    layers: list[DecoderLayer],
    *,
    requires_any: tuple[str, ...] = (),
    description: str = "",
    verification: str = "contract",
) -> ProtocolSpec:
    counts: dict[str, int] = {}
    for layer in layers:
        for key in layer.options:
            counts[key] = counts.get(key, 0) + 1
    flat: dict[str, OptionSpec] = {}
    owners: dict[str, int] = {}
    for index, layer in enumerate(layers):
        for key, spec in layer.options.items():
            public = key if counts[key] == 1 else f"{layer.decoder}.{key}"
            flat[public] = spec
            owners[public] = index
    return ProtocolSpec(name, tuple(layers), flat, owners, requires_any, description, verification)


_SPI_LAYER = DecoderLayer(
    "spi",
    {"clk": True, "miso": False, "mosi": False, "cs": False},
    {
        "cs_polarity": _enum("active-low", "active-high", default="active-low"),
        "cpol": _enum(0, 1, default=0),
        "cpha": _enum(0, 1, default=0),
        "bitorder": _enum("msb-first", "lsb-first", default="msb-first"),
        "wordsize": _integer(1, 128, 8),
    },
)

_UART_LAYER = DecoderLayer(
    "uart",
    {"rx": False, "tx": False},
    {
        "baudrate": _integer(1, 100_000_000, 115200),
        "data_bits": _enum(5, 6, 7, 8, 9, default=8),
        "parity": _enum("none", "odd", "even", "zero", "one", "ignore", default="none"),
        "stop_bits": _enum(0.0, 0.5, 1.0, 1.5, 2.0, default=1.0),
        "bit_order": _enum("lsb-first", "msb-first", default="lsb-first"),
        "format": _enum("ascii", "dec", "hex", "oct", "bin", default="hex"),
        "invert_rx": _enum("yes", "no", default="no"),
        "invert_tx": _enum("yes", "no", default="no"),
        "sample_point": _number(1, 99, 50),
    },
)

_JTAG_LAYER = DecoderLayer(
    "jtag",
    {"tdi": True, "tdo": True, "tck": True, "tms": True, "trst": False, "srst": False, "rtck": False},
    {},
)

_SUPPORTED: dict[str, ProtocolSpec] = {
    "i2c": _protocol(
        "i2c",
        [
            DecoderLayer(
                "i2c",
                {"scl": True, "sda": True},
                {"address_format": _enum("shifted", "unshifted", default="shifted")},
            )
        ],
        description="I2C / TWI transaction-level decoding.",
    ),
    "i2s": _protocol(
        "i2s",
        [DecoderLayer("i2s", {"sck": True, "ws": True, "sd": True})],
        description="I2S audio sample decoding.",
    ),
    "spi": _protocol(
        "spi",
        [_SPI_LAYER],
        requires_any=("mosi", "miso"),
        description="SPI byte stream decoding.",
    ),
    "qspi": _protocol(
        "qspi",
        [
            DecoderLayer(
                "smart_qspi",
                {"clk": True, "io0": True, "io1": False, "io2": False, "io3": False, "cs": False},
                {
                    "cs_polarity": _enum("active-low", "active-high", default="active-low"),
                    "cpol": _enum(0, 1, default=0),
                    "cpha": _enum(0, 1, default=0),
                    "bitorder": _enum("msb-first", "lsb-first", default="msb-first"),
                    "ads": _enum("32-Bit Address", "24-Bit Address", default="24-Bit Address"),
                    "frame": _enum("yes", "no", default="no"),
                    "twolinesmode": _enum("spi", "dspi", "qspi", default="spi"),
                    "invalidlevel": _enum("both", "low", "high", default="both"),
                },
            )
        ],
        description="DreamSourceLab Smart QSPI, adapted from DSView v1.3.2.",
    ),
    "can": _protocol(
        "can",
        [
            DecoderLayer(
                "can",
                {"can_rx": True},
                {
                    "nominal_bitrate": _integer(1, 20_000_000, 1_000_000),
                    "fast_bitrate": _integer(1, 50_000_000, 2_000_000),
                    "sample_point": _number(1, 99, 70.0),
                },
            )
        ],
        description="CAN 2.0 and CAN-FD frame decoding from a CAN-RX logic signal.",
    ),
    "jtag": _protocol("jtag", [_JTAG_LAYER], description="JTAG TAP state and shift-data decoding."),
    "swd": _protocol(
        "swd",
        [
            DecoderLayer(
                "swd",
                {"swclk": True, "swdio": True},
                {"strict_start": _enum("yes", "no", default="no")},
            )
        ],
        description="ARM Serial Wire Debug v1 transaction decoding.",
    ),
    "onewire_link": _protocol(
        "onewire_link",
        [
            DecoderLayer(
                "onewire_link",
                {"owr": True},
                {"overdrive": _enum("yes", "no", default="no")},
            )
        ],
        description="1-Wire link layer (reset, presence and bits).",
    ),
    "ps2": _protocol(
        "ps2",
        [DecoderLayer("ps2", {"clk": True, "data": True})],
        description="PS/2 keyboard or mouse frame decoding.",
    ),
    "mdio": _protocol(
        "mdio",
        [
            DecoderLayer(
                "mdio",
                {"mdc": True, "mdio": True},
                {"show_debug_bits": _enum("yes", "no", default="no")},
            )
        ],
        description="IEEE 802.3 MDIO management-frame decoding.",
    ),
    "ir_nec": _protocol(
        "ir_nec",
        [
            DecoderLayer(
                "ir_nec",
                {"ir": True},
                {
                    "polarity": _enum("auto", "active-low", "active-high", default="active-low"),
                    "cd_freq": _integer(0, 10_000_000, 0),
                    "extended": _enum("yes", "no", default="no"),
                },
            )
        ],
        description="NEC infrared remote-control decoding.",
    ),
    "spdif": _protocol(
        "spdif",
        [DecoderLayer("spdif", {"data": True})],
        description="S/PDIF biphase-mark audio decoding.",
    ),
    "usb_signalling": _protocol(
        "usb_signalling",
        [
            DecoderLayer(
                "usb_signalling",
                {"dp": True, "dm": True},
                {"signalling": _enum("automatic", "full-speed", "low-speed", default="automatic")},
            )
        ],
        description="USB low/full-speed line signalling (not packet or class decoding).",
    ),
    "cjtag": _protocol(
        "cjtag",
        [DecoderLayer("cjtag", {"tckc": True, "tmsc": True})],
        description="Compact JTAG (IEEE 1149.7) two-wire decoding.",
    ),
    "sdcard_sd": _protocol(
        "sdcard_sd",
        [
            DecoderLayer(
                "sdcard_sd",
                {"cmd": True, "clk": True, "dat0": False, "dat1": False, "dat2": False, "dat3": False},
            )
        ],
        description="SD card native-mode command and response decoding.",
    ),
    "spiflash": _protocol(
        "spiflash",
        [
            _SPI_LAYER,
            DecoderLayer(
                "spiflash",
                {},
                {
                    "chip": _enum(
                        "fidelix_fm25q32",
                        "winbond_w25q80dv",
                        "adesto_at45db161e",
                        "macronix_mx25l3205d",
                        "atmel_at25256",
                        "macronix_mx25l1605d",
                        "macronix_mx25l6405d",
                        "atmel_at25128",
                        "macronix_mx25l8006",
                        default="fidelix_fm25q32",
                    ),
                    "format": _enum("hex", "ascii", default="hex"),
                },
            ),
        ],
        requires_any=("mosi", "miso"),
        description="SPI NOR flash/EEPROM command, address and payload decoding (stacked on SPI).",
    ),
    "sdcard_spi": _protocol(
        "sdcard_spi",
        [_SPI_LAYER, DecoderLayer("sdcard_spi", {})],
        requires_any=("mosi", "miso"),
        description="SD card SPI-mode command and response decoding (stacked on SPI).",
    ),
    "modbus": _protocol(
        "modbus",
        [
            _UART_LAYER,
            DecoderLayer(
                "modbus",
                {},
                {
                    "scchannel": _enum("RX", "TX", default="RX"),
                    "cschannel": _enum("RX", "TX", default="TX"),
                    "framegap": _integer(1, 1000, 28),
                },
            ),
        ],
        requires_any=("rx", "tx"),
        description="Modbus RTU request/response decoding (stacked on UART).",
    ),
    "lin": _protocol(
        "lin",
        [_UART_LAYER, DecoderLayer("lin", {}, {"version": _enum(1, 2, default=2)})],
        requires_any=("rx", "tx"),
        description="LIN bus frame decoding (stacked on UART).",
    ),
    "onewire_network": _protocol(
        "onewire_network",
        [
            DecoderLayer("onewire_link", {"owr": True}, {"overdrive": _enum("yes", "no", default="no")}),
            DecoderLayer("onewire_network", {}),
        ],
        description="1-Wire ROM command and device addressing (stacked on onewire_link).",
    ),
    "jtag_stm32": _protocol(
        "jtag_stm32",
        [_JTAG_LAYER, DecoderLayer("jtag_stm32", {})],
        description="ST STM32 JTAG command decoding (stacked on JTAG).",
    ),
    "jtag_ejtag": _protocol(
        "jtag_ejtag",
        [_JTAG_LAYER, DecoderLayer("jtag_ejtag", {})],
        description="MIPS EJTAG decoding (stacked on JTAG).",
    ),
}


_VERIFIED_PROTOCOLS = {
    "i2c", "i2s", "spi", "qspi", "can", "spiflash", "modbus", "ps2", "mdio",
}
for _name, _spec in _SUPPORTED.items():
    if _name in _VERIFIED_PROTOCOLS:
        _SUPPORTED[_name] = ProtocolSpec(
            _spec.name, _spec.layers, _spec.options, _spec.option_layers,
            _spec.requires_any, _spec.description, "synthetic-waveform",
        )

_LINE = re.compile(
    r"^(?P<start>\d+)(?:-(?P<end>\d+))?\s+(?P<decoder>[^:]+):\s*(?:\[(?P<cls>[^\]]+)\]\s*)?(?P<text>.*)$"
)


def protocol_specs() -> dict[str, ProtocolSpec]:
    return dict(_SUPPORTED)


def supported_protocols() -> list[str]:
    return sorted(_SUPPORTED)


@dataclass
class DecoderRuntime:
    executable: str
    decoder_dir: Path | None = None
    source: str = "path"

    @classmethod
    def discover(cls) -> "DecoderRuntime | None":
        candidates: list[tuple[str, Path | None, str]] = []
        explicit = os.environ.get("LOGIC_AGENT_DECODER_EXE")
        if explicit:
            candidates.append((explicit, None, "env"))
        here = Path(__file__).resolve().parent.parent
        # Portable layout: <root>/bin/sigrok-cli.exe, <root>/decoders.
        # Also support running directly from app/ in the packaged bundle.
        for base in (here, here.parent, Path.cwd(), Path.cwd().parent):
            exe = base / "bin" / "sigrok-cli.exe"
            if exe.is_file():
                decoder_dir = base / "decoders"
                candidates.append((str(exe), decoder_dir, "portable"))
                break
        found = shutil.which("sigrok-cli")
        if found:
            candidates.append((found, None, "path"))
        for exe, decoders, source in candidates:
            path = Path(exe)
            if not path.is_file():
                continue
            env_dir = os.environ.get("SIGROKDECODE_DIR")
            chosen = Path(env_dir) if env_dir else (decoders if decoders and decoders.is_dir() else None)
            return cls(str(path), chosen, source)
        return None

    @property
    def available(self) -> bool:
        return bool(self.executable) and Path(self.executable).is_file()

    def require(self) -> None:
        if not self.available:
            raise BackendUnavailableError(
                "protocol decoder runtime is not installed; expected sigrok-cli.exe in bin/ or on PATH"
            )

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.decoder_dir is not None:
            env["SIGROKDECODE_DIR"] = str(self.decoder_dir)
        return env

    def list_decoders(self) -> list[str]:
        self.require()
        result = subprocess.run(
            [self.executable, "-L"], capture_output=True, text=True, timeout=30, env=self.env()
        )
        if result.returncode != 0:
            raise CaptureError("sigrok-cli failed to list decoders", details={"stderr": result.stderr[-2000:]})
        ids: list[str] = []
        in_decoders = False
        for line in result.stdout.splitlines():
            if "Supported protocol decoders" in line:
                in_decoders = True
                continue
            if not in_decoders:
                continue
            stripped = line.strip()
            if not stripped:
                if ids:
                    break
                continue
            # sigrok-cli emits ID and description separated by whitespace.
            # Do not depend on a fixed indentation width.
            ids.append(stripped.split()[0])
        return ids

    def show(self, decoder: str) -> dict[str, Any]:
        self.require()
        result = subprocess.run(
            [self.executable, "--show", "-P", decoder], capture_output=True, text=True, timeout=30, env=self.env()
        )
        if result.returncode != 0:
            raise CaptureError(
                f"decoder {decoder} could not be loaded",
                details={"stderr": (result.stderr or result.stdout)[-3000:]},
            )
        info: dict[str, Any] = {"stdout": result.stdout}
        for line in result.stdout.splitlines():
            if line.startswith("ID: "):
                info["id"] = line[4:].strip()
            elif line.startswith("Name: "):
                info["name"] = line[6:].strip()
            elif line.startswith("License: "):
                info["license"] = line[9:].strip()
        return info


@dataclass
class DecodeRequest:
    capture_id: str
    protocol: str
    channel_map: dict[str, str]
    options: dict[str, Any] = field(default_factory=dict)
    start_s: float = 0.0
    duration_s: float | None = None
    max_events: int = 500
    include_bits: bool = False
    include_lower_layers: bool = False


def _normalize_option(value: Any, spec: OptionSpec, name: str) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValidationError(f"option {name} must not be empty")
        if any(char in value for char in ",:\r\n"):
            raise ValidationError(f"option {name} contains a reserved character")
    if spec.kind == "enum":
        candidate = value
        if isinstance(value, bool):
            candidate = "yes" if value else "no"
        if candidate in spec.values:
            return candidate
        if isinstance(candidate, str):
            for allowed in spec.values:
                if str(allowed) == candidate:
                    return allowed
        raise ValidationError(
            f"invalid value for {name}: {value!r}",
            details={"allowed_values": list(spec.values)},
        )
    if spec.kind == "integer":
        if isinstance(value, bool):
            raise ValidationError(f"option {name} must be an integer")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"option {name} must be an integer") from exc
        if isinstance(value, float) and value != normalized:
            raise ValidationError(f"option {name} must be an integer")
        if spec.minimum is not None and normalized < spec.minimum:
            raise ValidationError(f"option {name} must be >= {spec.minimum}")
        if spec.maximum is not None and normalized > spec.maximum:
            raise ValidationError(f"option {name} must be <= {spec.maximum}")
        return normalized
    if spec.kind == "number":
        if isinstance(value, bool):
            raise ValidationError(f"option {name} must be a number")
        try:
            normalized_float = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"option {name} must be a number") from exc
        if spec.minimum is not None and normalized_float < spec.minimum:
            raise ValidationError(f"option {name} must be >= {spec.minimum}")
        if spec.maximum is not None and normalized_float > spec.maximum:
            raise ValidationError(f"option {name} must be <= {spec.maximum}")
        return normalized_float
    raise ValidationError(f"option {name} has an unsupported schema type")


def _validate_request(request: DecodeRequest) -> tuple[ProtocolSpec, dict[str, Any]]:
    spec = _SUPPORTED.get(request.protocol)
    if spec is None:
        raise ValidationError(
            f"unsupported protocol: {request.protocol}",
            details={"supported_protocols": supported_protocols()},
        )
    missing = [name for name, required in spec.channels.items() if required and name not in request.channel_map]
    if missing:
        raise ValidationError(
            f"{request.protocol} requires channels: {', '.join(missing)}",
            details={"required_channels": [n for n, r in spec.channels.items() if r]},
        )
    if spec.requires_any and not any(name in request.channel_map for name in spec.requires_any):
        raise ValidationError(
            f"{request.protocol} requires at least one of: {', '.join(spec.requires_any)}",
            details={"requires_one_of": list(spec.requires_any)},
        )
    unknown = [name for name in request.channel_map if name not in spec.channels]
    if unknown:
        raise ValidationError(
            f"unknown {request.protocol} channel roles: {', '.join(unknown)}",
            details={"allowed_channels": sorted(spec.channels)},
        )
    normalized: dict[str, Any] = {}
    for name, value in request.options.items():
        try:
            layer_index, layer_name, option_spec = spec.resolve_option(name)
        except KeyError:
            raise ValidationError(
                f"unknown {request.protocol} option: {name}",
                details={"allowed_options": sorted(spec.options)},
            ) from None
        del layer_index
        normalized[layer_name] = _normalize_option(value, option_spec, name)
    if request.start_s < 0:
        raise ValidationError("start_s must be non-negative")
    if request.duration_s is not None and request.duration_s <= 0:
        raise ValidationError("duration_s must be positive")
    if request.max_events < 1 or request.max_events > 5000:
        raise ValidationError("max_events must be between 1 and 5000")
    return spec, normalized


def _channel_ids(manifest: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in manifest.get("channels", [])}


def _window(samples: bytes, rate: int, start_s: float, duration_s: float | None) -> bytes:
    start = min(len(samples), max(0, round(start_s * rate)))
    if duration_s is None:
        end = len(samples)
    else:
        end = min(len(samples), start + max(1, round(duration_s * rate)))
    if end <= start:
        raise ValidationError("decode window is empty")
    return samples[start:end]


def _write_binary(path: Path, channels: list[bytes]) -> int:
    if not channels:
        raise ValidationError("no channels selected for decoding")
    if any(len(item) != len(channels[0]) for item in channels):
        raise ValidationError("channel data lengths differ; capture is inconsistent")
    count = len(channels[0])
    out = bytearray(count)
    for bit, values in enumerate(channels):
        mask = 1 << bit
        for index, value in enumerate(values):
            if value:
                out[index] |= mask
    path.write_bytes(out)
    return count


def _decoder_argument(spec: ProtocolSpec, channel_map: dict[str, str], role_order: list[str], options: dict[str, Any]) -> str:
    name_to_index = {name: index for index, name in enumerate(role_order)}
    grouped: dict[int, dict[str, Any]] = {}
    for key, value in options.items():
        layer_index, layer_option, _ = spec.resolve_option(key)
        grouped.setdefault(layer_index, {})[layer_option] = value
    rendered: list[str] = []
    for index, layer in enumerate(spec.layers):
        parts = [layer.decoder]
        if index == 0:
            parts.extend(f"{role}={name_to_index[role]}" for role in role_order)
        for key, value in grouped.get(index, {}).items():
            parts.append(f"{key}={value}")
        rendered.append(":".join(parts))
    return ",".join(rendered)


def decode_capture(
    *,
    store: Any,
    runtime: DecoderRuntime | None,
    request: DecodeRequest,
) -> dict[str, Any]:
    spec, normalized_options = _validate_request(request)
    if runtime is None:
        raise BackendUnavailableError(
            "no protocol decoder runtime found; set LOGIC_AGENT_DECODER_EXE or bundle sigrok-cli"
        )
    runtime.require()
    manifest = store.manifest(request.capture_id)
    available = _channel_ids(manifest)
    mapped = list(request.channel_map.values())
    missing = [name for name in mapped if name not in available]
    if missing:
        raise ValidationError(
            f"capture has no channel(s): {', '.join(missing)}",
            details={"available_channels": sorted(available)},
        )
    if len(set(mapped)) != len(mapped):
        raise ValidationError("each protocol role must use a different capture channel")

    rate = int(manifest["sample_rate_hz"])
    role_order = [name for name in spec.channels if name in request.channel_map]
    loaded = [store.load_channel(request.capture_id, request.channel_map[role])[0] for role in role_order]
    windows = [_window(values, rate, request.start_s, request.duration_s) for values in loaded]
    sample_count = len(windows[0])
    offset = min(len(loaded[0]), max(0, round(request.start_s * rate)))

    decoder_arg = _decoder_argument(spec, request.channel_map, role_order, normalized_options)
    if request.include_lower_layers:
        annotations = ",".join(layer.decoder for layer in spec.layers)
    else:
        annotations = spec.decoder

    with tempfile.TemporaryDirectory(prefix="logic-agent-decode-") as temp:
        binary = Path(temp) / "capture.bin"
        _write_binary(binary, windows)
        command = [
            runtime.executable,
            "-i", str(binary),
            "-I", f"binary:numchannels={len(windows)}:samplerate={rate}",
            "-P", decoder_arg,
            "-A", annotations,
            "--protocol-decoder-ann-class",
            "--protocol-decoder-samplenum",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                env=runtime.env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise CaptureError("protocol decode timed out") from exc
    if result.returncode != 0 and not result.stdout.strip():
        raise CaptureError(
            f"{request.protocol} decoder failed",
            details={"stderr": (result.stderr or "")[-4000:]},
        )
    stdout_lines = result.stdout.splitlines()
    if not stdout_lines and result.stderr.strip():
        stderr = result.stderr.strip()
        if "Traceback" in stderr or "TypeError" in stderr or "AttributeError" in stderr:
            raise CaptureError(
                f"{request.protocol} decoder crashed",
                details={"stderr": stderr[-4000:]},
            )
    events = _parse_events(
        stdout_lines,
        rate,
        offset,
        request.include_bits,
        request.include_lower_layers,
        spec.decoder,
    )
    total = len(events)
    truncated = total > request.max_events
    if truncated:
        events = events[: request.max_events]
    warnings: list[str] = []
    if truncated:
        warnings.append(f"truncated at max_events={request.max_events}; total_events={total}")
    stderr_tail = result.stderr.strip()
    if stderr_tail:
        warnings.extend(line for line in stderr_tail.splitlines() if line.strip())[:20]
    return {
        "ok": True,
        "capture_id": request.capture_id,
        "protocol": request.protocol,
        "decoder": spec.decoder,
        "layers": [layer.decoder for layer in spec.layers],
        "runtime": {"source": runtime.source, "executable": runtime.executable},
        "channels": dict(request.channel_map),
        "options": dict(normalized_options),
        "window": {
            "start_s": round(offset / rate, 9),
            "duration_s": round(sample_count / rate, 9),
            "samples": sample_count,
        },
        "event_count": total,
        "events": events,
        "truncated": truncated,
        "warnings": warnings,
    }


def _parse_events(
    lines: list[str],
    rate: int,
    offset: int,
    include_bits: bool,
    include_lower_layers: bool,
    primary_decoder: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    address_summaries: set[tuple[str, int, int]] = set()
    pending: list[dict[str, Any]] = []
    for raw in lines:
        match = _LINE.match(raw.strip())
        if not match:
            continue
        source = match.group("decoder").strip()
        layer = re.sub(r"-\d+$", "", source)
        if not include_lower_layers and layer != primary_decoder:
            continue
        cls = (match.group("cls") or "").strip()
        text = match.group("text").strip()
        if not cls and ": " in text:
            prefix, remainder = text.split(": ", 1)
            if prefix and " " not in prefix:
                cls, text = prefix, remainder
        if _is_bit_class(cls) and not include_bits:
            continue
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        event: dict[str, Any] = {
            "type": cls or "annotation",
            "text": text,
            "source": source,
            "layer": layer,
            "start_s": round((start + offset) / rate, 9),
            "end_s": round((end + offset) / rate, 9),
        }
        numeric = _extract_numeric(cls, text)
        if numeric is not None:
            event["value"] = numeric
        # libsigrok's I2C decoder emits a short "address: Write" helper
        # annotation immediately before the complete "Address write: 50".
        # Keep the complete event and suppress the duplicate helper.
        if cls in {"address-write", "address-read"} and text in {"Write", "Read"}:
            pending.append(event)
            continue
        if cls in {"address-write", "address-read"}:
            address_summaries.add((cls, start, end))
            pending = [item for item in pending if item["type"] != cls]
        events.extend(pending)
        pending = []
        events.append(event)
    events.extend(pending)
    if address_summaries:
        events = [
            event
            for event in events
            if not (
                event["type"] in {"address-write", "address-read"}
                and event["text"] in {"Write", "Read"}
            )
        ]
    return events


def _is_bit_class(cls: str) -> bool:
    if not cls:
        return False
    return (
        cls in {"bit", "bits", "decoded-bit", "bit-tdi", "bit-tdo"}
        or cls.endswith("-bit")
        or cls.endswith("-bits")
    )


_HEX = re.compile(r"0x([0-9a-fA-F]+)")
_ADDR = re.compile(r"(?:Address (?:write|read)|Addr):\s*0x([0-9a-fA-F]+)")


def _extract_numeric(cls: str, text: str) -> int | None:
    match = _ADDR.search(text)
    if match:
        return int(match.group(1), 16)
    if cls in {
        "address-write", "address-read", "data-write", "data-read", "data-transfer",
        "mosi-data", "miso-data", "data", "id", "full-id", "dlc", "addr", "cmd",
        "read", "write", "word", "sample", "decode", "frame",
    }:
        match = _HEX.search(text)
        if match:
            return int(match.group(1), 16)
    if cls in {"left", "right"}:
        match = re.search(r"([0-9a-fA-F]{8})$", text)
        if match:
            return int(match.group(1), 16)
    return None
