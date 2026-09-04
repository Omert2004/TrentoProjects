"""Packet protocol shared by the 2 kHz firmware and host tools.

Protocol v1, all multi-byte values little-endian::

    AA 55 D4 packet_seq:u16 first_sample_index:u32 sample_count:u8
    cumulative_drop_count:u32 (IFI:u16 IFQ:u16) * sample_count crc:u16

CRC16-CCITT-FALSE covers marker ``D4`` through the final payload byte. It uses
initial value ``0xFFFF``, polynomial ``0x1021``, no reflection, and no final
XOR. A full 32-sample packet is 144 bytes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import struct
import time
from typing import BinaryIO


SYNC = b"\xAA\x55"
MARKER = 0xD4
MAX_SAMPLES_PER_PACKET = 32
FIXED_PACKET_BYTES = 16
ADC_MIN = 0
ADC_MAX = 4095


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def packet_size(sample_count: int) -> int:
    if not 1 <= sample_count <= MAX_SAMPLES_PER_PACKET:
        raise ValueError("sample_count must be in 1..32")
    return FIXED_PACKET_BYTES + 4 * sample_count


def serial_capacity_samples_per_second(
    baud: int,
    samples_per_packet: int = MAX_SAMPLES_PER_PACKET,
) -> float:
    if baud <= 0:
        raise ValueError("baud must be positive")
    return (baud / 10.0) * samples_per_packet / packet_size(samples_per_packet)


@dataclass(frozen=True)
class RawPacket:
    packet_sequence: int
    first_sample_index: int
    cumulative_drop_count: int
    samples: tuple[tuple[int, int], ...]
    host_time_ns: int
    raw: bytes


@dataclass
class PacketReaderStats:
    bytes_read: int = 0
    packets_accepted: int = 0
    samples_accepted: int = 0
    initial_alignment_bytes: int = 0
    bytes_discarded_after_sync: int = 0
    invalid_headers: int = 0
    crc_errors: int = 0
    invalid_adc_packets: int = 0
    resync_events: int = 0
    serial_timeouts: int = 0
    packet_sequence_gaps: int = 0
    missing_packets: int = 0
    packet_sequence_reorders: int = 0
    sample_index_gaps: int = 0
    missing_samples: int = 0
    sample_index_reorders: int = 0
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
            or self.invalid_adc_packets
            or self.resync_events
            or self.packet_sequence_gaps
            or self.packet_sequence_reorders
            or self.sample_index_gaps
            or self.sample_index_reorders
            or self.reported_drop_increase
        )


class RawPacketReader:
    """Stateful, overlap-safe parser for packetized firmware output."""

    def __init__(self, serial_port: BinaryIO, *, read_size: int = 512) -> None:
        if read_size <= 0:
            raise ValueError("read_size must be positive")
        self.serial_port = serial_port
        self.read_size = read_size
        self.buffer = bytearray()
        self.stats = PacketReaderStats()
        self._last_packet_sequence: int | None = None
        self._last_sample_index: int | None = None

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
        if self.stats.packets_accepted == 0 and not definite_corruption:
            self.stats.initial_alignment_bytes += count
        else:
            self.stats.bytes_discarded_after_sync += count

    def _reject_candidate(self, category: str) -> None:
        if category == "header":
            self.stats.invalid_headers += 1
        elif category == "crc":
            self.stats.crc_errors += 1
        elif category == "adc":
            self.stats.invalid_adc_packets += 1
        else:
            raise ValueError(f"unknown rejection category: {category}")
        self.stats.resync_events += 1
        self._discard(1, definite_corruption=True)

    @staticmethod
    def _forward_delta(current: int, expected: int, modulus: int) -> tuple[int, bool]:
        delta = (current - expected) % modulus
        return delta, delta < modulus // 2

    def _track_sequences(self, packet: RawPacket) -> None:
        if self._last_packet_sequence is not None:
            expected_packet = (self._last_packet_sequence + 1) & 0xFFFF
            if packet.packet_sequence != expected_packet:
                delta, forward = self._forward_delta(
                    packet.packet_sequence, expected_packet, 1 << 16
                )
                if forward:
                    self.stats.packet_sequence_gaps += 1
                    self.stats.missing_packets += delta
                else:
                    self.stats.packet_sequence_reorders += 1

        if self._last_sample_index is not None:
            expected_sample = (self._last_sample_index + 1) & 0xFFFFFFFF
            if packet.first_sample_index != expected_sample:
                delta, forward = self._forward_delta(
                    packet.first_sample_index, expected_sample, 1 << 32
                )
                if forward:
                    self.stats.sample_index_gaps += 1
                    self.stats.missing_samples += delta
                else:
                    self.stats.sample_index_reorders += 1

        if self.stats.first_reported_drop_count is None:
            self.stats.first_reported_drop_count = packet.cumulative_drop_count
        elif self.stats.last_reported_drop_count is not None:
            delta = (
                packet.cumulative_drop_count - self.stats.last_reported_drop_count
            ) & 0xFFFFFFFF
            if delta < (1 << 31):
                self.stats.reported_drop_increase += delta

        self.stats.last_reported_drop_count = packet.cumulative_drop_count
        self._last_packet_sequence = packet.packet_sequence
        self._last_sample_index = (
            packet.first_sample_index + len(packet.samples) - 1
        ) & 0xFFFFFFFF

    def read_packet(self) -> RawPacket | None:
        """Return one validated packet or ``None`` after a serial timeout."""

        minimum_header_bytes = 14
        while True:
            sync_at = self.buffer.find(SYNC)
            if sync_at < 0:
                keep = 1 if self.buffer.endswith(SYNC[:1]) else 0
                self._discard(len(self.buffer) - keep)
                if not self._fill():
                    return None
                continue

            if sync_at:
                after_first_packet = self.stats.packets_accepted > 0
                self._discard(sync_at)
                if after_first_packet:
                    self.stats.resync_events += 1

            if len(self.buffer) < minimum_header_bytes:
                if not self._fill():
                    return None
                continue

            if self.buffer[2] != MARKER:
                self._reject_candidate("header")
                continue

            sample_count = self.buffer[9]
            if not 1 <= sample_count <= MAX_SAMPLES_PER_PACKET:
                self._reject_candidate("header")
                continue

            total_size = packet_size(sample_count)
            if len(self.buffer) < total_size:
                if not self._fill():
                    return None
                continue

            candidate = bytes(self.buffer[:total_size])
            expected_crc = struct.unpack_from("<H", candidate, total_size - 2)[0]
            actual_crc = crc16_ccitt(candidate[2:-2])
            if actual_crc != expected_crc:
                self._reject_candidate("crc")
                continue

            packet_sequence = struct.unpack_from("<H", candidate, 3)[0]
            first_sample_index = struct.unpack_from("<I", candidate, 5)[0]
            cumulative_drop_count = struct.unpack_from("<I", candidate, 10)[0]
            values = struct.unpack_from(f"<{sample_count * 2}H", candidate, 14)
            samples = tuple(
                (values[2 * index], values[2 * index + 1])
                for index in range(sample_count)
            )
            if any(
                not (ADC_MIN <= ifi <= ADC_MAX and ADC_MIN <= ifq <= ADC_MAX)
                for ifi, ifq in samples
            ):
                self._reject_candidate("adc")
                continue

            del self.buffer[:total_size]
            packet = RawPacket(
                packet_sequence=packet_sequence,
                first_sample_index=first_sample_index,
                cumulative_drop_count=cumulative_drop_count,
                samples=samples,
                host_time_ns=time.perf_counter_ns(),
                raw=candidate,
            )
            self.stats.packets_accepted += 1
            self.stats.samples_accepted += sample_count
            self._track_sequences(packet)
            return packet


def encode_packet(
    *,
    packet_sequence: int,
    first_sample_index: int,
    cumulative_drop_count: int,
    samples: tuple[tuple[int, int], ...],
) -> bytes:
    """Reference encoder used by tests and synthetic stream generation."""

    if not 1 <= len(samples) <= MAX_SAMPLES_PER_PACKET:
        raise ValueError("samples must contain 1..32 pairs")
    if any(not (ADC_MIN <= i <= ADC_MAX and ADC_MIN <= q <= ADC_MAX) for i, q in samples):
        raise ValueError("sample outside the 12-bit ADC range")

    body = bytearray()
    body.append(MARKER)
    body.extend(struct.pack("<HI", packet_sequence & 0xFFFF, first_sample_index & 0xFFFFFFFF))
    body.append(len(samples))
    body.extend(struct.pack("<I", cumulative_drop_count & 0xFFFFFFFF))
    for ifi, ifq in samples:
        body.extend(struct.pack("<HH", ifi, ifq))
    return SYNC + bytes(body) + struct.pack("<H", crc16_ccitt(bytes(body)))
