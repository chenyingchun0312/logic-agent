from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from logic_agent.decoders import (
    DecodeRequest,
    DecoderRuntime,
    decode_capture,
    protocol_specs,
    supported_protocols,
)
from logic_agent.errors import BackendUnavailableError, ValidationError
from logic_agent.store import CaptureStore


def _runtime() -> DecoderRuntime | None:
    runtime = DecoderRuntime.discover()
    if runtime is None:
        pytest.skip("sigrok-cli decoder runtime is not installed")
    return runtime


def _write_capture(tmp_path: Path, channels: dict[str, bytes], rate: int) -> tuple[CaptureStore, str]:
    from logic_agent.models import CaptureRequest, CapturedSignal, Channel

    store = CaptureStore(tmp_path / "captures")
    captured = CapturedSignal(
        backend="test",
        device_id="synthetic",
        device_label="synthetic",
        channels=tuple(Channel(name, name, index) for index, name in enumerate(channels)),
        sample_rate_hz=rate,
        samples=channels,
    )
    request = CaptureRequest(
        channels=tuple(channels),
        sample_rate_hz=rate,
        duration_s=len(next(iter(channels.values()))) / rate,
    )
    manifest = store.save(captured, request)
    return store, manifest["id"]


def _spi_mode0(command: int, address: bytes, data: bytes) -> bytes:
    clk: list[int] = []
    io0: list[int] = []
    cs: list[int] = []

    def idle(count: int) -> None:
        clk.extend([0] * count)
        io0.extend([0] * count)
        cs.extend([1] * count)

    def byte(value: int) -> None:
        for bit in range(7, -1, -1):
            clk.extend([0, 1])
            io0.extend([(value >> bit) & 1] * 2)
            cs.extend([0, 0])
        # Keep CS asserted; real SPI flash transactions do not release CS
        # between command, address and data bytes.
        clk.append(0)
        io0.append(0)
        cs.append(0)

    idle(4)
    for value in (command, *address, *data):
        byte(value)
    idle(4)
    return bytes((clk[i] | (io0[i] << 1) | (cs[i] << 2)) for i in range(len(clk)))


def _qspi_mode0(command: int, address: bytes, data: bytes) -> dict[str, bytes]:
    packed = _spi_mode0(command, address, data)
    return {
        "CLK": bytes(1 if value & 1 else 0 for value in packed),
        "IO0": bytes(1 if value & 2 else 0 for value in packed),
        "CS": bytes(1 if value & 4 else 0 for value in packed),
    }


def _i2c_bytes(address: int, value: int) -> dict[str, bytes]:
    scl: list[int] = []
    sda: list[int] = []

    def idle(count: int) -> None:
        scl.extend([1] * count)
        sda.extend([1] * count)

    def start() -> None:
        # SCL high, SDA high-to-low.
        sda.extend([1, 0])
        scl.extend([1, 1])

    def stop() -> None:
        # SCL high, SDA low-to-high.
        sda.extend([0, 1])
        scl.extend([1, 1])

    def byte(bits: int) -> None:
        for bit in range(7, -1, -1):
            value_bit = (bits >> bit) & 1
            # SDA changes while SCL is low, and is stable at the rising edge.
            sda.extend([value_bit] * 3)
            scl.extend([0, 1, 0])

    def ack(nack: bool) -> None:
        setup = 1 if nack else 0
        sda.extend([setup] * 3)
        scl.extend([0, 1, 0])

    idle(4)
    start()
    byte(address << 1)
    ack(False)
    byte(value)
    ack(False)
    stop()
    idle(4)
    return {"SCL": bytes(scl), "SDA": bytes(sda)}


def test_supported_protocol_surface_includes_requested_protocols():
    expected = {
        "i2c", "i2s", "spi", "qspi", "can", "jtag", "swd", "onewire_link",
        "ps2", "mdio", "ir_nec", "spdif", "usb_signalling", "cjtag",
        "sdcard_sd", "spiflash", "sdcard_spi", "modbus", "lin",
        "onewire_network", "jtag_stm32", "jtag_ejtag",
    }
    assert expected <= set(supported_protocols())
    specs = protocol_specs()
    assert specs["qspi"].decoder == "smart_qspi"
    assert specs["i2c"].channels["scl"] is True
    assert specs["i2s"].channels["sd"] is True
    assert [layer.decoder for layer in specs["spiflash"].layers] == ["spi", "spiflash"]
    assert specs["modbus"].decoder == "modbus"
    assert specs["can"].options["sample_point"].kind == "number"


def test_stacked_protocol_prefixed_options():
    spec = protocol_specs()["modbus"]
    index, name, option = spec.resolve_option("uart.baudrate")
    assert index == 0 and name == "baudrate"
    assert option.kind == "integer"
    assert spec.describe()["options"]["baudrate"]["decoder"] == "uart"
    assert spec.describe()["options"]["framegap"]["decoder"] == "modbus"


def test_typed_option_validation():
    from logic_agent.decoders import _validate_request

    spec, options = _validate_request(
        DecodeRequest(
            capture_id="x",
            protocol="can",
            channel_map={"can_rx": "D0"},
            options={"nominal_bitrate": "500000", "sample_point": "75.5"},
        )
    )
    assert spec.decoder == "can"
    assert options == {"nominal_bitrate": 500000, "sample_point": 75.5}
    with pytest.raises(ValidationError):
        _validate_request(
            DecodeRequest(
                capture_id="x",
                protocol="can",
                channel_map={"can_rx": "D0"},
                options={"nominal_bitrate": 0},
            )
        )


def test_decode_i2c_capture(tmp_path):
    runtime = _runtime()
    channels = _i2c_bytes(0x50, 0x4B)
    store, capture_id = _write_capture(tmp_path, channels, 1_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="i2c",
            channel_map={"scl": "SCL", "sda": "SDA"},
            max_events=100,
        ),
    )
    types = [event["type"] for event in result["events"]]
    assert result["ok"] is True
    assert "start" in types
    assert "stop" in types
    # sigrok displays the shifted I2C address by default (0x50 = 0xA0 >> 1).
    address = next(event for event in result["events"] if event["type"] == "address-write")
    assert "50" in address["text"]
    assert any(event["type"] == "data-write" for event in result["events"])


def test_decode_i2s_capture(tmp_path):
    runtime = _runtime()
    # One left channel word and one right channel word, MSB first.
    sck = bytes([0, 1] * 16)
    ws = bytes([0] * 16 + [1] * 16)
    sd = bytes([1] * 32)
    store, capture_id = _write_capture(tmp_path, {"SCK": sck, "WS": ws, "SD": sd}, 2_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="i2s",
            channel_map={"sck": "SCK", "ws": "WS", "sd": "SD"},
            max_events=100,
        ),
    )
    assert result["event_count"] >= 1
    assert any(event["type"] in {"left", "right"} for event in result["events"])


def test_decode_qspi_adapter_with_standard_spi_waveform(tmp_path):
    runtime = _runtime()
    if runtime is None:
        pytest.skip("decoder runtime unavailable")
    if "smart_qspi" not in runtime.list_decoders():
        pytest.skip("smart_qspi decoder not installed")
    channels = _qspi_mode0(0x03, b"\x00\x12\x34", b"\xa5\x5a")
    store, capture_id = _write_capture(tmp_path, channels, 4_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="qspi",
            channel_map={"clk": "CLK", "io0": "IO0", "cs": "CS"},
            options={"twolinesmode": "spi"},
            max_events=100,
        ),
    )
    texts = [event["text"] for event in result["events"]]
    assert any("Read Data Bytes" in text for text in texts)
    assert any("0x1234" in text for text in texts)
    assert any("0xa5" in text.lower() for text in texts)


def test_decode_window_offset_and_event_limit(tmp_path):
    runtime = _runtime()
    channels = _i2c_bytes(0x50, 0x4B)
    store, capture_id = _write_capture(tmp_path, channels, 1_000_000)
    full = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="i2c",
            channel_map={"scl": "SCL", "sda": "SDA"},
            max_events=100,
        ),
    )
    assert full["event_count"] > 2
    limited = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="i2c",
            channel_map={"scl": "SCL", "sda": "SDA"},
            max_events=2,
        ),
    )
    assert limited["truncated"] is True
    assert len(limited["events"]) == 2
    assert limited["events"][0]["start_s"] <= full["events"][0]["start_s"]


def test_decode_rejects_unknown_channel_role(tmp_path):
    runtime = _runtime()
    store, capture_id = _write_capture(tmp_path, {"D0": b"\x00\x01"}, 1_000_000)
    with pytest.raises(ValidationError):
        decode_capture(
            store=store,
            runtime=runtime,
            request=DecodeRequest(
                capture_id=capture_id,
                protocol="i2c",
                channel_map={"scl": "D0", "tx": "D0"},
            ),
        )


def test_decode_reports_missing_runtime(tmp_path):
    store, capture_id = _write_capture(tmp_path, {"A": b"\x00\x01"}, 1_000_000)
    with pytest.raises(BackendUnavailableError):
        decode_capture(
            store=store,
            runtime=None,
            request=DecodeRequest(
                capture_id=capture_id,
                protocol="i2c",
                channel_map={"scl": "A", "sda": "A"},
            ),
        )


@pytest.mark.parametrize(
    "decoder",
    [
        "i2c", "i2s", "spi", "smart_qspi", "can", "jtag", "swd",
        "onewire_link", "ps2", "mdio", "ir_nec", "spdif", "usb_signalling",
        "cjtag", "sdcard_sd", "spiflash", "sdcard_spi", "modbus", "lin",
        "onewire_network", "jtag_stm32", "jtag_ejtag",
    ],
)
def test_decoder_contracts_are_loadable(tmp_path, decoder):
    runtime = _runtime()
    assert runtime is not None
    if decoder == "smart_qspi":
        assert "smart_qspi" in runtime.list_decoders()
    info = runtime.show(decoder)
    assert info["id"] == decoder
    assert info["license"]


def _spi_four_wire(transactions: list[tuple[bytes, bytes]]) -> dict[str, bytes]:
    """Encode (MOSI, MISO) byte pairs as SPI mode 0, one CS frame each."""
    clk: list[int] = []
    mosi: list[int] = []
    miso: list[int] = []
    cs: list[int] = []

    def idle(count: int) -> None:
        clk.extend([0] * count)
        mosi.extend([0] * count)
        miso.extend([0] * count)
        cs.extend([1] * count)

    idle(4)
    for outgoing, incoming in transactions:
        for out_byte, in_byte in zip(outgoing, incoming):
            for bit in range(7, -1, -1):
                clk.extend([0, 1])
                mosi.extend([(out_byte >> bit) & 1] * 2)
                miso.extend([(in_byte >> bit) & 1] * 2)
                cs.extend([0, 0])
        idle(4)
    return {
        "CLK": bytes(clk),
        "MOSI": bytes(mosi),
        "MISO": bytes(miso),
        "CS": bytes(cs),
    }


def test_decode_spiflash_stacked_transaction(tmp_path):
    runtime = _runtime()
    channels = _spi_four_wire(
        [(bytes([0x03, 0x00, 0x12, 0x34, 0x00, 0x00]), bytes([0x00] * 4 + [0xA5, 0x5A]))]
    )
    store, capture_id = _write_capture(tmp_path, channels, 4_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="spiflash",
            channel_map={"clk": "CLK", "mosi": "MOSI", "miso": "MISO", "cs": "CS"},
            options={"chip": "winbond_w25q80dv"},
            max_events=100,
        ),
    )
    assert result["layers"] == ["spi", "spiflash"]
    texts = [event["text"] for event in result["events"]]
    assert any("Read data" in text for text in texts)
    assert any("0x001234" in text and "a5 5a" in text.lower() for text in texts)
    assert all(event["layer"] == "spiflash" for event in result["events"])
    with_lower = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="spiflash",
            channel_map={"clk": "CLK", "mosi": "MOSI", "miso": "MISO", "cs": "CS"},
            options={"chip": "winbond_w25q80dv"},
            max_events=100,
            include_lower_layers=True,
        ),
    )
    assert any(event["layer"] == "spi" for event in with_lower["events"])
    assert any(event["layer"] == "spiflash" for event in with_lower["events"])


def _uart_bytes(value: int, baud: int, sample_rate: int, bits: int = 8) -> list[int]:
    # Keep samples-per-bit integral so the synthetic line and decoder agree.
    assert sample_rate % baud == 0
    samples_per_bit = sample_rate // baud
    bits_out = [0]
    for bit in range(bits):
        bits_out.append((value >> bit) & 1)
    bits_out.append(1)
    result: list[int] = [1] * (samples_per_bit * 2)
    for bit in bits_out:
        result.extend([bit] * samples_per_bit)
    result.extend([1] * (samples_per_bit * 4))
    return result


def _can_stuff(bits: list[int]) -> list[int]:
    out: list[int] = []
    run_value = None
    run_count = 0
    for bit in bits:
        if bit == run_value:
            run_count += 1
        else:
            run_value, run_count = bit, 1
        out.append(bit)
        if run_count == 5:
            out.append(1 - bit)
            run_value, run_count = None, 0
    return out


def _can_crc15(bits: list[int]) -> int:
    # CAN 2.0A CRC-15, polynomial 0x4599, zero initial value.
    polynomial = 0x4599
    crc = 0
    for bit in bits:
        top = ((crc >> 14) & 1) ^ bit
        crc = (crc << 1) & 0x7FFF
        if top:
            crc ^= polynomial
    return crc


def _can_standard_frame(frame_id: int, payload: bytes) -> list[int]:
    bits = [0]
    bits.extend((frame_id >> shift) & 1 for shift in range(10, -1, -1))
    bits.extend([0, 0])  # RTR dominant (data frame), IDE dominant (standard)
    bits.append(0)  # Reserved bit R0, dominant in classic CAN
    bits.extend((len(payload) >> shift) & 1 for shift in range(3, -1, -1))
    for value in payload:
        bits.extend((value >> shift) & 1 for shift in range(7, -1, -1))
    crc = _can_crc15(bits)
    crc_bits = [(crc >> shift) & 1 for shift in range(14, -1, -1)]
    bits.extend(crc_bits)
    # Per ISO 11898, stuffing applies from SOF through the CRC sequence;
    # the CRC delimiter, ACK slot/delimiter and EOF are not stuffed.
    stuffed_head = _can_stuff(bits)
    stuffed_head.extend([1, 0, 1])  # CRC delimiter, ACK, ACK delimiter
    stuffed_head.extend([1] * 7)
    return stuffed_head


def test_decode_can_standard_frame(tmp_path):
    runtime = _runtime()
    bits = _can_standard_frame(0x123, bytes([0x11, 0x22]))
    samples_per_bit = 10
    # Leave an initial recessive run for the decoder to arm its sampling
    # phase before SOF, then pad the inter-frame gap.
    line = bytearray([1] * 40)
    for bit in bits + [1] * 3:
        line.extend([bit] * samples_per_bit)
    line.extend([1] * 40)
    store, capture_id = _write_capture(tmp_path, {"CAN": bytes(line)}, 1_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="can",
            channel_map={"can_rx": "CAN"},
            options={"nominal_bitrate": 100_000, "sample_point": 70},
            max_events=100,
        ),
    )
    texts = [event["text"] for event in result["events"]]
    assert any("Start of frame" in text for text in texts)
    assert any("0x123" in text for text in texts)
    assert any("Data byte 0: 0x11" in text for text in texts)
    assert any("Data byte 1: 0x22" in text for text in texts)
    assert any("End of frame" in text or text == "EOF" for text in texts)
    assert not any("must be" in text for text in texts)


def _ps2_frame(value: int) -> tuple[bytes, bytes]:
    # PS/2 device-to-host: start, 8 data bits LSB-first, odd parity, stop.
    bits = [0]
    bits.extend((value >> bit) & 1 for bit in range(8))
    parity = 1 if bin(value).count("1") % 2 == 0 else 0
    bits.extend([parity, 1])
    clk: list[int] = [1, 1]
    data: list[int] = [1, 0]  # Data falls before the first clock pulse.
    for bit in bits:
        data.extend([bit] * 4)
        clk.extend([1, 1, 0, 0])
    data.extend([1] * 4)
    clk.extend([1, 1, 1, 1])
    return bytes(clk), bytes(data)


def test_decode_ps2_frame(tmp_path):
    runtime = _runtime()
    clk, data = _ps2_frame(0x1C)
    store, capture_id = _write_capture(tmp_path, {"CLK": clk, "DATA": data}, 1_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="ps2",
            channel_map={"clk": "CLK", "data": "DATA"},
            max_events=100,
        ),
    )
    typed = [(event["type"], event["text"]) for event in result["events"]]
    assert any(kind == "word" and "1c" in text.lower() for kind, text in typed)
    assert any(kind == "parity-ok" for kind, _ in typed)


def test_decode_mdio_clause22_read(tmp_path):
    runtime = _runtime()
    # Clause 22 read: 32-bit preamble, ST=01, OP=10, PHYAD=1, REGAD=2,
    # TA=Z0, DATA=0x1234. Clock rises in the middle of every MDIO bit.
    bits = [1] * 32 + [0, 1, 1, 0]
    bits.extend((1 >> shift) & 1 for shift in range(4, -1, -1))
    bits.extend((2 >> shift) & 1 for shift in range(4, -1, -1))
    bits.extend([1, 0])
    bits.extend((0x1234 >> shift) & 1 for shift in range(15, -1, -1))
    mdc: list[int] = []
    mdio: list[int] = []
    for bit in bits:
        mdc.extend([0, 1])
        mdio.extend([bit, bit])
    store, capture_id = _write_capture(tmp_path, {"MDC": bytes(mdc), "MDIO": bytes(mdio)}, 1_000_000)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="mdio",
            channel_map={"mdc": "MDC", "mdio": "MDIO"},
            max_events=100,
        ),
    )
    combined = " ".join(event["text"] for event in result["events"])
    assert "DATA: 1234" in combined
    assert "PHYAD: 01" in combined
    assert "REGAD: 02" in combined


def test_decode_modbus_stacked_uart_request(tmp_path):
    runtime = _runtime()
    # Modbus RTU: slave 0x01, function 0x03, start 0x0000, count 0x0002,
    # CRC 0xC40B. One TX line is enough to exercise the UART->Modbus stack.
    payload = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x02, 0xC4, 0x0B])
    sample_rate, baud = 1_152_000, 115_200
    line: list[int] = []
    for value in payload:
        line.extend(_uart_bytes(value, baud, sample_rate))
    store, capture_id = _write_capture(tmp_path, {"TX": bytes(line)}, sample_rate)
    result = decode_capture(
        store=store,
        runtime=runtime,
        request=DecodeRequest(
            capture_id=capture_id,
            protocol="modbus",
            channel_map={"tx": "TX"},
            options={"baudrate": baud},
            max_events=100,
        ),
    )
    assert result["layers"] == ["uart", "modbus"]
    combined = " ".join(event["text"] for event in result["events"])
    assert "Slave ID: 1" in combined
    assert "Function 3: Read Holding Registers" in combined
    assert "Read 2 units of data" in combined
    assert "CRC correct" in combined
