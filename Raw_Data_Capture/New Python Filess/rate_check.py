"""Verify the receive rate and integrity of packetized raw I/Q samples.

Counts MCU-indexed samples only after packet CRC and sequence validation.
"""

from __future__ import annotations

import argparse
import sys
import time

from raw_protocol import (
    MAX_SAMPLES_PER_PACKET,
    RawPacketReader,
    packet_size,
    serial_capacity_samples_per_second,
)
from raw_serial_capture import nonnegative_float, positive_float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=positive_float, required=True)
    parser.add_argument("--duration", type=positive_float, default=10.0)
    parser.add_argument(
        "--tolerance-percent",
        type=nonnegative_float,
        default=2.0,
        help="Maximum allowed receive-rate error (default: 2%%).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud <= 0:
        print("Error: --baud must be positive.", file=sys.stderr)
        return 2

    capacity = serial_capacity_samples_per_second(args.baud)
    utilization = 100.0 * args.sampling_rate / capacity
    print(
        f"Target: {args.sampling_rate:g} samples/s; packetized UART ceiling: "
        f"{capacity:.1f} samples/s ({utilization:.1f}% requested utilization)."
    )
    if args.sampling_rate > capacity:
        print("FAIL: target rate is physically impossible with this baud/frame format.")

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
        return 2

    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    reader = RawPacketReader(
        serial_port,
        read_size=packet_size(MAX_SAMPLES_PER_PACKET),
    )
    serial_port.reset_input_buffer()

    count = 0
    packet_count = 0
    started_ns = time.perf_counter_ns()
    deadline_ns = started_ns + int(args.duration * 1_000_000_000)
    try:
        while time.perf_counter_ns() < deadline_ns:
            packet = reader.read_packet()
            if packet is not None:
                packet_count += 1
                count += len(packet.samples)
    except KeyboardInterrupt:
        print("\nStopped early by user.")
    finally:
        ended_ns = time.perf_counter_ns()
        serial_port.close()

    elapsed_s = max((ended_ns - started_ns) / 1_000_000_000, 1e-12)
    observed = count / elapsed_s
    error_percent = 100.0 * (observed - args.sampling_rate) / args.sampling_rate

    print(f"Packets received: {packet_count}")
    print(f"Validated samples received: {count}")
    print(f"Elapsed: {elapsed_s:.3f}s")
    print(f"Observed receive rate: {observed:.2f} samples/s")
    print(f"Rate error: {error_percent:+.2f}%")
    print(f"Parser statistics: {reader.stats.to_dict()}")

    passed = bool(
        args.sampling_rate <= capacity
        and abs(error_percent) <= args.tolerance_percent
        and not reader.stats.corruption_detected
        and count > 0
    )
    if passed:
        print("PASS: rate, CRC, packet/sample sequences, and MCU drop count are clean.")
        return 0

    print("FAIL: do not treat captures at this configuration as validated.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
