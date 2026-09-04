"""Parser for the AI_Phase one-shot STFT parity protocol."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import BinaryIO

import numpy as np

SYNC = b"\xAA\x55"
RAW_MARKER = 0xE0
Q15_MARKER = 0xE1
COLUMN_MARKER = 0xE2
PROTOCOL_VERSION = 1
STAGE_WINDOWED = 1
STAGE_FFT = 2


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class RawFrame:
    sequence: int
    sampling_rate_hz: int
    fft_size: int
    fft_hop: int
    flags: int
    first_sample_index: int
    cumulative_drop_count: int
    i: np.ndarray
    q: np.ndarray


@dataclass(frozen=True)
class Q15Frame:
    sequence: int
    stage: int
    values: np.ndarray


@dataclass(frozen=True)
class ColumnFrame:
    sequence: int
    values: np.ndarray


@dataclass
class ParserStats:
    bytes_read: int = 0
    frames_accepted: int = 0
    initial_alignment_bytes: int = 0
    discarded_after_sync: int = 0
    invalid_headers: int = 0
    crc_errors: int = 0
    serial_empty_reads: int = 0

    @property
    def corruption_detected(self) -> bool:
        return bool(self.discarded_after_sync or self.invalid_headers or self.crc_errors)


class ParityFrameReader:
    def __init__(self, port: BinaryIO, *, read_size: int = 512) -> None:
        self.port = port
        self.read_size = read_size
        self.buffer = bytearray()
        self.stats = ParserStats()

    def _fill(self) -> bool:
        chunk = self.port.read(self.read_size)
        if not chunk:
            self.stats.serial_empty_reads += 1
            return False
        self.buffer.extend(chunk)
        self.stats.bytes_read += len(chunk)
        return True

    def _discard(self, count: int, *, corrupt: bool = False) -> None:
        if count <= 0:
            return
        del self.buffer[:count]
        if self.stats.frames_accepted == 0 and not corrupt:
            self.stats.initial_alignment_bytes += count
        else:
            self.stats.discarded_after_sync += count

    def _reject(self, *, crc: bool = False) -> None:
        if crc:
            self.stats.crc_errors += 1
        else:
            self.stats.invalid_headers += 1
        self._discard(1, corrupt=True)

    def read_frame(self) -> RawFrame | Q15Frame | ColumnFrame | None:
        while True:
            sync_at = self.buffer.find(SYNC)
            if sync_at < 0:
                keep = 1 if self.buffer.endswith(SYNC[:1]) else 0
                self._discard(len(self.buffer) - keep)
                if not self._fill():
                    return None
                continue
            if sync_at:
                self._discard(sync_at)

            if len(self.buffer) < 9:
                if not self._fill():
                    return None
                continue

            marker = self.buffer[2]
            version = self.buffer[3]
            if version != PROTOCOL_VERSION or marker not in (RAW_MARKER, Q15_MARKER, COLUMN_MARKER):
                self._reject()
                continue

            if marker == RAW_MARKER:
                if len(self.buffer) < 25:
                    if not self._fill():
                        return None
                    continue
                fft_size = struct.unpack_from("<H", self.buffer, 10)[0]
                flags = self.buffer[14]
                count = struct.unpack_from("<H", self.buffer, 15)[0]
                total_size = 27 + 4 * count
                expected_count = fft_size + 1 if flags & 1 else fft_size
                valid_header = fft_size == 256 and count == expected_count
            elif marker == Q15_MARKER:
                stage = self.buffer[6]
                count = struct.unpack_from("<H", self.buffer, 7)[0]
                total_size = 11 + 2 * count
                valid_header = stage in (STAGE_WINDOWED, STAGE_FFT) and count == 512
            else:
                count = struct.unpack_from("<H", self.buffer, 6)[0]
                total_size = 10 + count
                valid_header = count == 256

            if not valid_header:
                self._reject()
                continue
            if len(self.buffer) < total_size:
                if not self._fill():
                    return None
                continue

            candidate = bytes(self.buffer[:total_size])
            expected = struct.unpack_from("<H", candidate, total_size - 2)[0]
            if crc16_ccitt(candidate[2:-2]) != expected:
                self._reject(crc=True)
                continue

            sequence = struct.unpack_from("<H", candidate, 4)[0]
            if marker == RAW_MARKER:
                sampling_rate = struct.unpack_from("<I", candidate, 6)[0]
                fft_size, fft_hop = struct.unpack_from("<HH", candidate, 10)
                flags = candidate[14]
                first_index, drops = struct.unpack_from("<II", candidate, 17)
                pairs = np.frombuffer(candidate, dtype="<u2", count=2 * count, offset=25).copy()
                frame = RawFrame(sequence, sampling_rate, fft_size, fft_hop, flags,
                                 first_index, drops, pairs[0::2], pairs[1::2])
            elif marker == Q15_MARKER:
                values = np.frombuffer(candidate, dtype="<i2", count=count, offset=9).copy()
                frame = Q15Frame(sequence, candidate[6], values)
            else:
                values = np.frombuffer(candidate, dtype=np.uint8, count=count, offset=8).copy()
                frame = ColumnFrame(sequence, values)

            del self.buffer[:total_size]
            self.stats.frames_accepted += 1
            return frame


@dataclass
class ParityCapture:
    raw: RawFrame
    windowed: Q15Frame
    fft: Q15Frame
    column: ColumnFrame


class CaptureAssembler:
    def __init__(self) -> None:
        self.groups: dict[int, dict[str, object]] = {}

    def add(self, frame: RawFrame | Q15Frame | ColumnFrame) -> ParityCapture | None:
        group = self.groups.setdefault(frame.sequence, {})
        if isinstance(frame, RawFrame):
            group["raw"] = frame
        elif isinstance(frame, ColumnFrame):
            group["column"] = frame
        elif frame.stage == STAGE_WINDOWED:
            group["windowed"] = frame
        else:
            group["fft"] = frame
        if all(name in group for name in ("raw", "windowed", "fft", "column")):
            return ParityCapture(group["raw"], group["windowed"], group["fft"], group["column"])
        return None
