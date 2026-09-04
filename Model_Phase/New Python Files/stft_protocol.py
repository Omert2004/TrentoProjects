"""
Protocol v1, all multi-byte values little-endian::

    AA 55 D0 column_seq:u16 first_new_sample_index:u32
             cumulative_drop_count:u32 column[256]:u8 crc:u16
    AA 55 D2 report_seq:u16 accepted_samples_last_second:u16
             cumulative_drop_count:u32 crc:u16
    AA 55 D3 report_seq:u16 hop_count:u16 stft_ticks:u16
             dma_wait_ticks:u16 crc:u16
    AA 55 D4 inference_seq:u16 last_column_seq:u16 predicted_class:u8
             logits[4]:i32 crc:u16

CRC16-CCITT-FALSE covers the marker through the final payload byte. Sync and
CRC bytes are excluded. Values in a column must be in the on-chip STFT range
0..31. The parser is stateful: partial reads survive timeouts, startup
alignment is distinguished from post-sync corruption, and column/sample
continuity is checked with wraparound.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import struct
import time
from typing import BinaryIO, Union


SYNC = b"\xAA\x55"
COLUMN_MARKER = 0xD0
RATE_MARKER = 0xD2
PROFILE_MARKER = 0xD3
CNN_RESULT_MARKER = 0xD4
COLUMN_SIZE = 256
FFT_HOP = 128
COLUMN_MIN = 0
COLUMN_MAX = 31
COLUMN_PACKET_BYTES = 271
RATE_PACKET_BYTES = 13
PROFILE_PACKET_BYTES = 13
CNN_RESULT_PACKET_BYTES = 26


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def serial_capacity_columns_per_second(baud: int) -> float:
    if baud <= 0:
        raise ValueError("baud must be positive")
    return (baud / 10.0) / COLUMN_PACKET_BYTES


@dataclass(frozen=True)
class ColumnPacket:
    column_sequence: int
    first_new_sample_index: int
    cumulative_drop_count: int
    values: tuple[int, ...]
    host_time_ns: int
    raw: bytes


@dataclass(frozen=True)
class RatePacket:
    report_sequence: int
    accepted_samples: int
    cumulative_drop_count: int
    host_time_ns: int
    raw: bytes


@dataclass(frozen=True)
class ProfilePacket:
    report_sequence: int
    hop_count: int
    stft_ticks: int
    dma_wait_ticks: int
    host_time_ns: int
    raw: bytes


@dataclass(frozen=True)
class CnnResultPacket:
    inference_sequence: int
    last_column_sequence: int
    predicted_class: int
    logits: tuple[int, int, int, int]
    host_time_ns: int
    raw: bytes


Frame = Union[ColumnPacket, RatePacket, ProfilePacket, CnnResultPacket]


@dataclass
class FrameReaderStats:
    bytes_read: int = 0
    frames_accepted: int = 0
    columns_accepted: int = 0
    rate_frames_accepted: int = 0
    profile_frames_accepted: int = 0
    cnn_result_frames_accepted: int = 0
    initial_alignment_bytes: int = 0
    bytes_discarded_after_sync: int = 0
    invalid_headers: int = 0
    crc_errors: int = 0
    invalid_column_packets: int = 0
    resync_events: int = 0
    serial_timeouts: int = 0
    column_sequence_gaps: int = 0
    missing_columns: int = 0
    column_sequence_reorders: int = 0
    sample_index_gaps: int = 0
    missing_samples: int = 0
    sample_index_reorders: int = 0
    rate_sequence_gaps: int = 0
    rate_sequence_reorders: int = 0
    profile_sequence_gaps: int = 0
    profile_sequence_reorders: int = 0
    inference_sequence_gaps: int = 0
    inference_sequence_reorders: int = 0
    first_reported_drop_count: int | None = None
    last_reported_drop_count: int | None = None
    reported_drop_increase: int = 0

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)

    @property
    def corruption_detected(self) -> bool:
        return bool(
            self.bytes_discarded_after_sync
            or self.invalid_headers
            or self.crc_errors
            or self.invalid_column_packets
            or self.resync_events
            or self.column_sequence_gaps
            or self.column_sequence_reorders
            or self.sample_index_gaps
            or self.sample_index_reorders
            or self.rate_sequence_gaps
            or self.rate_sequence_reorders
            or self.profile_sequence_gaps
            or self.profile_sequence_reorders
            or self.inference_sequence_gaps
            or self.inference_sequence_reorders
            or self.reported_drop_increase
        )


class StftFrameReader:
    """Persistent, overlap-safe reader for multiplexed D0/D2/D3/D4 frames."""

    def __init__(self, serial_port: BinaryIO, *, read_size: int = 512) -> None:
        if read_size <= 0:
            raise ValueError("read_size must be positive")
        self.serial_port = serial_port
        self.read_size = read_size
        self.buffer = bytearray()
        self.stats = FrameReaderStats()
        self._last_column_sequence: int | None = None
        self._last_first_new_sample_index: int | None = None
        self._last_rate_sequence: int | None = None
        self._last_profile_sequence: int | None = None
        self._last_inference_sequence: int | None = None

    def _fill(self) -> bool:
        chunk = self.serial_port.read(self.read_size)
        if not chunk:
            self.stats.serial_timeouts += 1
            return False
        self.buffer.extend(chunk)
        self.stats.bytes_read += len(chunk)
        return True

    def _discard(self, count: int, *, definite_corruption: bool = False) -> None:
        if count <= 0:
            return
        del self.buffer[:count]
        if self.stats.frames_accepted == 0 and not definite_corruption:
            self.stats.initial_alignment_bytes += count
        else:
            self.stats.bytes_discarded_after_sync += count

    def _reject_candidate(self, category: str) -> None:
        if category == "header":
            self.stats.invalid_headers += 1
        elif category == "crc":
            self.stats.crc_errors += 1
        elif category == "column":
            self.stats.invalid_column_packets += 1
        else:
            raise ValueError(f"unknown rejection category: {category}")
        self.stats.resync_events += 1
        self._discard(1, definite_corruption=True)

    @staticmethod
    def _forward_delta(
        current: int, expected: int, modulus: int
    ) -> tuple[int, bool]:
        delta = (current - expected) % modulus
        return delta, delta < modulus // 2

    def _track_column(self, packet: ColumnPacket) -> None:
        if self._last_column_sequence is not None:
            expected = (self._last_column_sequence + 1) & 0xFFFF
            if packet.column_sequence != expected:
                delta, forward = self._forward_delta(
                    packet.column_sequence, expected, 1 << 16
                )
                if forward:
                    self.stats.column_sequence_gaps += 1
                    self.stats.missing_columns += delta
                else:
                    self.stats.column_sequence_reorders += 1

        if self._last_first_new_sample_index is not None:
            expected = (self._last_first_new_sample_index + FFT_HOP) & 0xFFFFFFFF
            if packet.first_new_sample_index != expected:
                delta, forward = self._forward_delta(
                    packet.first_new_sample_index, expected, 1 << 32
                )
                if forward:
                    self.stats.sample_index_gaps += 1
                    self.stats.missing_samples += delta
                else:
                    self.stats.sample_index_reorders += 1

        self._track_drop_count(packet.cumulative_drop_count)
        self._last_column_sequence = packet.column_sequence
        self._last_first_new_sample_index = packet.first_new_sample_index

    def _track_drop_count(self, count: int) -> None:
        if self.stats.first_reported_drop_count is None:
            self.stats.first_reported_drop_count = count
        elif self.stats.last_reported_drop_count is not None:
            delta = (count - self.stats.last_reported_drop_count) & 0xFFFFFFFF
            if delta < (1 << 31):
                self.stats.reported_drop_increase += delta
        self.stats.last_reported_drop_count = count

    def _track_report_sequence(self, current: int, *, kind: str) -> None:
        attribute = f"_last_{kind}_sequence"
        previous = getattr(self, attribute)
        if previous is not None:
            expected = (previous + 1) & 0xFFFF
            if current != expected:
                _delta, forward = self._forward_delta(current, expected, 1 << 16)
                counter = (
                    f"{kind}_sequence_gaps"
                    if forward
                    else f"{kind}_sequence_reorders"
                )
                setattr(self.stats, counter, getattr(self.stats, counter) + 1)
        setattr(self, attribute, current)

    def read_frame(self) -> Frame | None:
        """Return one validated frame, or ``None`` after a serial timeout."""

        while True:
            sync_at = self.buffer.find(SYNC)
            if sync_at < 0:
                keep = 1 if self.buffer.endswith(SYNC[:1]) else 0
                self._discard(len(self.buffer) - keep)
                if not self._fill():
                    return None
                continue

            if sync_at:
                after_first = self.stats.frames_accepted > 0
                self._discard(sync_at)
                if after_first:
                    self.stats.resync_events += 1

            if len(self.buffer) < 3:
                if not self._fill():
                    return None
                continue

            marker = self.buffer[2]
            sizes = {
                COLUMN_MARKER: COLUMN_PACKET_BYTES,
                RATE_MARKER: RATE_PACKET_BYTES,
                PROFILE_MARKER: PROFILE_PACKET_BYTES,
                CNN_RESULT_MARKER: CNN_RESULT_PACKET_BYTES,
            }
            total_size = sizes.get(marker)
            if total_size is None:
                self._reject_candidate("header")
                continue

            if len(self.buffer) < total_size:
                if not self._fill():
                    return None
                continue

            candidate = bytes(self.buffer[:total_size])
            expected_crc = struct.unpack_from("<H", candidate, total_size - 2)[0]
            if crc16_ccitt(candidate[2:-2]) != expected_crc:
                self._reject_candidate("crc")
                continue

            now = time.perf_counter_ns()
            if marker == COLUMN_MARKER:
                sequence = struct.unpack_from("<H", candidate, 3)[0]
                first_index = struct.unpack_from("<I", candidate, 5)[0]
                drops = struct.unpack_from("<I", candidate, 9)[0]
                values = tuple(candidate[13 : 13 + COLUMN_SIZE])
                if any(value < COLUMN_MIN or value > COLUMN_MAX for value in values):
                    self._reject_candidate("column")
                    continue
                frame: Frame = ColumnPacket(
                    sequence, first_index, drops, values, now, candidate
                )
            elif marker == RATE_MARKER:
                report_sequence, accepted, drops = struct.unpack_from(
                    "<HHI", candidate, 3
                )
                frame = RatePacket(report_sequence, accepted, drops, now, candidate)
            elif marker == PROFILE_MARKER:
                report_sequence, hops, stft_ticks, dma_ticks = struct.unpack_from(
                    "<HHHH", candidate, 3
                )
                frame = ProfilePacket(
                    report_sequence, hops, stft_ticks, dma_ticks, now, candidate
                )
            else:
                inference_sequence, last_column_sequence, predicted_class = struct.unpack_from(
                    "<HHB", candidate, 3
                )
                logits = struct.unpack_from("<iiii", candidate, 8)
                if predicted_class >= 4:
                    self._reject_candidate("header")
                    continue
                frame = CnnResultPacket(
                    inference_sequence,
                    last_column_sequence,
                    predicted_class,
                    logits,
                    now,
                    candidate,
                )

            del self.buffer[:total_size]
            self.stats.frames_accepted += 1
            if isinstance(frame, ColumnPacket):
                self.stats.columns_accepted += 1
                self._track_column(frame)
            elif isinstance(frame, RatePacket):
                self.stats.rate_frames_accepted += 1
                self._track_drop_count(frame.cumulative_drop_count)
                self._track_report_sequence(frame.report_sequence, kind="rate")
            elif isinstance(frame, ProfilePacket):
                self.stats.profile_frames_accepted += 1
                self._track_report_sequence(frame.report_sequence, kind="profile")
            else:
                self.stats.cnn_result_frames_accepted += 1
                self._track_report_sequence(frame.inference_sequence, kind="inference")
            return frame


def _with_crc(body: bytes) -> bytes:
    return SYNC + body + struct.pack("<H", crc16_ccitt(body))


def encode_column(
    *,
    column_sequence: int,
    first_new_sample_index: int,
    cumulative_drop_count: int,
    values: tuple[int, ...],
) -> bytes:
    if len(values) != COLUMN_SIZE:
        raise ValueError(f"values must contain {COLUMN_SIZE} entries")
    if any(value < COLUMN_MIN or value > COLUMN_MAX for value in values):
        raise ValueError("column value outside 0..31")
    body = bytes([COLUMN_MARKER]) + struct.pack(
        "<HII",
        column_sequence & 0xFFFF,
        first_new_sample_index & 0xFFFFFFFF,
        cumulative_drop_count & 0xFFFFFFFF,
    ) + bytes(values)
    return _with_crc(body)


def encode_rate(
    *, report_sequence: int, accepted_samples: int, cumulative_drop_count: int
) -> bytes:
    body = bytes([RATE_MARKER]) + struct.pack(
        "<HHI",
        report_sequence & 0xFFFF,
        accepted_samples & 0xFFFF,
        cumulative_drop_count & 0xFFFFFFFF,
    )
    return _with_crc(body)


def encode_profile(
    *, report_sequence: int, hop_count: int, stft_ticks: int, dma_wait_ticks: int
) -> bytes:
    body = bytes([PROFILE_MARKER]) + struct.pack(
        "<HHHH",
        report_sequence & 0xFFFF,
        hop_count & 0xFFFF,
        stft_ticks & 0xFFFF,
        dma_wait_ticks & 0xFFFF,
    )
    return _with_crc(body)


def encode_cnn_result(
    *,
    inference_sequence: int,
    last_column_sequence: int,
    predicted_class: int,
    logits: tuple[int, int, int, int],
) -> bytes:
    if predicted_class not in range(4) or len(logits) != 4:
        raise ValueError("invalid CNN result")
    body = bytes([CNN_RESULT_MARKER]) + struct.pack(
        "<HHBiiii",
        inference_sequence & 0xFFFF,
        last_column_sequence & 0xFFFF,
        predicted_class,
        *logits,
    )
    return _with_crc(body)
