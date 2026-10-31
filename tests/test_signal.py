from logic_agent.signal import edge_indices, pack_bits, runs, summarize_channel, unpack_bits


def test_bit_round_trip_with_partial_final_byte():
    samples = bytes([1, 0, 1, 1, 0, 0, 1, 0, 1])
    packed = pack_bits(samples)
    assert len(packed) == 2
    assert unpack_bits(packed, len(samples)) == samples


def test_edge_and_run_analysis():
    samples = bytes([0, 0, 1, 1, 1, 0])
    rising, falling = edge_indices(samples)
    assert rising == [2]
    assert falling == [5]
    assert runs(samples) == [(0, 0, 2), (1, 2, 3), (0, 5, 1)]


def test_summary_estimates_square_wave_frequency():
    rate = 1_000_000
    samples = bytes([1 if i % 100 < 50 else 0 for i in range(20_000)])
    summary = summarize_channel("D0", samples, rate)
    assert abs(summary.transitions - 400) <= 1
    assert summary.estimated_frequency_hz is not None
    assert abs(summary.estimated_frequency_hz - 10_000) < 10
    assert 0.49 < summary.high_ratio < 0.51

