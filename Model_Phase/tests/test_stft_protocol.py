"""Offline regression tests for the host-side UART protocol parser."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


HOST_TOOLS = Path(__file__).resolve().parents[1] / "New Python Files"
sys.path.insert(0, str(HOST_TOOLS))

from stft_protocol import (  # noqa: E402
    CnnResultPacket,
    ColumnPacket,
    ProfilePacket,
    RatePacket,
    StftFrameReader,
    encode_cnn_result,
    encode_column,
    encode_profile,
    encode_rate,
)


class ChunkedSerial:
    """Minimal serial-like source that deliberately returns small chunks."""

    def __init__(self, payload: bytes, chunk_size: int = 7) -> None:
        self.payload = bytearray(payload)
        self.chunk_size = chunk_size

    def read(self, requested: int) -> bytes:
        if not self.payload:
            return b""
        count = min(requested, self.chunk_size, len(self.payload))
        result = bytes(self.payload[:count])
        del self.payload[:count]
        return result


class ProtocolTests(unittest.TestCase):
    def test_all_frame_types_round_trip(self) -> None:
        values = tuple(index % 32 for index in range(256))
        payload = b"startup-noise" + b"".join(
            (
                encode_column(
                    column_sequence=8,
                    first_new_sample_index=1024,
                    cumulative_drop_count=0,
                    values=values,
                ),
                encode_rate(
                    report_sequence=2,
                    accepted_samples=1993,
                    cumulative_drop_count=0,
                ),
                encode_profile(
                    report_sequence=2,
                    hop_count=15,
                    stft_ticks=1234,
                    dma_wait_ticks=0,
                ),
                encode_cnn_result(
                    inference_sequence=4,
                    last_column_sequence=22,
                    predicted_class=2,
                    logits=(-10, 20, 30, -40),
                ),
            )
        )

        reader = StftFrameReader(ChunkedSerial(payload))
        frames = [reader.read_frame() for _ in range(4)]

        self.assertIsInstance(frames[0], ColumnPacket)
        self.assertIsInstance(frames[1], RatePacket)
        self.assertIsInstance(frames[2], ProfilePacket)
        self.assertIsInstance(frames[3], CnnResultPacket)
        self.assertEqual(frames[0].values, values)
        self.assertEqual(frames[3].predicted_class, 2)
        self.assertEqual(frames[3].logits, (-10, 20, 30, -40))
        self.assertEqual(reader.stats.initial_alignment_bytes, len(b"startup-noise"))
        self.assertFalse(reader.stats.corruption_detected)

    def test_crc_corruption_is_rejected_and_reader_resynchronizes(self) -> None:
        damaged = bytearray(
            encode_rate(
                report_sequence=10,
                accepted_samples=2000,
                cumulative_drop_count=0,
            )
        )
        damaged[6] ^= 0x01
        valid = encode_cnn_result(
            inference_sequence=11,
            last_column_sequence=14,
            predicted_class=3,
            logits=(1, 2, 3, 4),
        )

        reader = StftFrameReader(ChunkedSerial(bytes(damaged) + valid))
        frame = reader.read_frame()

        self.assertIsInstance(frame, CnnResultPacket)
        self.assertEqual(reader.stats.crc_errors, 1)
        self.assertGreaterEqual(reader.stats.resync_events, 1)
        self.assertTrue(reader.stats.corruption_detected)


if __name__ == "__main__":
    unittest.main()
