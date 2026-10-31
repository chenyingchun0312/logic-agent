import pytest

from logic_agent.errors import ValidationError
from logic_agent.protocols import UartSettings, decode_uart


def make_uart(payload: bytes, baud: int, rate: int, idle_high: bool = True) -> bytes:
    spb = rate / baud
    idle = 1 if idle_high else 0
    bits: list[int] = [idle] * int(spb * 4)
    for value in payload:
        frame = [1 - idle, *[(value >> bit) & 1 for bit in range(8)], idle, idle]
        for bit in frame:
            bits.extend([bit] * round(spb))
        bits.extend([idle] * round(spb * 2))
    return bytes(bits)


@pytest.mark.parametrize("idle_high", [True, False])
def test_uart_round_trip_and_groups(idle_high):
    rate = 1_000_000
    payload = b"HELLO\n"
    samples = make_uart(payload, 115_200, rate, idle_high=idle_high)
    decoded, groups, warnings = decode_uart(
        samples,
        rate,
        UartSettings(baud=115_200, idle_high=idle_high),
    )
    assert bytes(item.value for item in decoded) == payload
    assert groups[0].text == "HELLO\n"
    assert warnings == []


def test_uart_rejects_wrong_idle_polarity():
    rate = 1_000_000
    samples = make_uart(b"OK", 115_200, rate)
    with pytest.raises(ValidationError):
        decode_uart(samples, rate, UartSettings(baud=115_200, idle_high=False))


def test_uart_rejects_too_low_oversampling():
    with pytest.raises(ValidationError):
        decode_uart(bytes([1, 1, 1, 1]), 100, UartSettings(baud=115_200))


def test_uart_rejects_non_positive_max_bytes():
    samples = make_uart(b"OK", 115_200, 1_000_000)
    with pytest.raises(ValidationError):
        decode_uart(samples, 1_000_000, UartSettings(), max_bytes=0)

