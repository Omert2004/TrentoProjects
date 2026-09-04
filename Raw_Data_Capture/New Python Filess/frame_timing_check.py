"""Measure host-side arrival timing for CRC-validated raw I/Q packets.

These timestamps describe when Python receives complete packets. USB, eZ-FET,
OS serial buffering, and scheduler latency can batch packets, so this tool must
not be interpreted as an ADC sample-jitter measurement. Long repeated host
gaps are still useful evidence of a stream stall when paired with firmware
counters or reset observations.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from raw_protocol import (
    MAX_SAMPLES_PER_PACKET,
    RawPacketReader,
    packet_size,
    serial_capacity_samples_per_second,
)
from raw_serial_capture import nonnegative_float, positive_float


def percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("no values")
    index = round((len(sorted_values) - 1) * fraction)
    return sorted_values[index]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=positive_float, required=True)
    parser.add_argument("--duration", type=positive_float, default=20.0)
    parser.add_argument("--threshold-ms", type=positive_float, default=50.0)
    parser.add_argument("--tolerance-percent", type=nonnegative_float, default=2.0)
    parser.add_argument(
        "--max-gap-lines",
        type=int,
        default=50,
        help="Maximum individual large gaps printed (default: 50).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud <= 0 or args.max_gap_lines < 0:
        print("Error: baud must be positive and max-gap-lines nonnegative.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
        return 2

    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    serial_port.reset_input_buffer()
    reader = RawPacketReader(
        serial_port,
        read_size=packet_size(MAX_SAMPLES_PER_PACKET),
    )

    gaps_ms: list[float] = []
    large_gaps: list[tuple[float, float]] = []
    last_packet_ns: int | None = None
    packet_count = 0
    sample_count = 0
    started_ns = time.perf_counter_ns()
    deadline_ns = started_ns + int(args.duration * 1_000_000_000)

    print(
        f"Measuring host packet arrivals for {args.duration:g}s. Expected full-packet "
        f"interval: {1000.0 * MAX_SAMPLES_PER_PACKET / args.sampling_rate:.3f} ms."
    )
    try:
        while time.perf_counter_ns() < deadline_ns:
            packet = reader.read_packet()
            if packet is None:
                continue
            packet_count += 1
            sample_count += len(packet.samples)
            if last_packet_ns is not None:
                gap_ms = (packet.host_time_ns - last_packet_ns) / 1_000_000
                gaps_ms.append(gap_ms)
                if gap_ms > args.threshold_ms:
                    t_s = (packet.host_time_ns - started_ns) / 1_000_000_000
                    large_gaps.append((t_s, gap_ms))
            last_packet_ns = packet.host_time_ns
    except KeyboardInterrupt:
        print("\nStopped early by user.")
    finally:
        ended_ns = time.perf_counter_ns()
        serial_port.close()

    elapsed_s = max((ended_ns - started_ns) / 1_000_000_000, 1e-12)
    observed_rate = sample_count / elapsed_s
    rate_error = 100.0 * (observed_rate - args.sampling_rate) / args.sampling_rate
    capacity = serial_capacity_samples_per_second(args.baud)

    print(f"\nPackets received: {packet_count}")
    print(f"Validated samples received: {sample_count}")
    print(f"Elapsed: {elapsed_s:.3f}s")
    print(f"Observed receive rate: {observed_rate:.2f} samples/s")
    print(f"Configured sampling rate: {args.sampling_rate:.2f} samples/s")
    print(f"Rate error: {rate_error:+.2f}%")
    print(f"Packetized UART ceiling: {capacity:.2f} samples/s")
    print(f"Parser statistics: {reader.stats.to_dict()}")

    if gaps_ms:
        ordered = sorted(gaps_ms)
        print(
            "Host packet inter-arrival gaps: "
            f"mean={statistics.fmean(gaps_ms):.3f} ms, "
            f"median={statistics.median(gaps_ms):.3f} ms, "
            f"p95={percentile(ordered, 0.95):.3f} ms, "
            f"p99={percentile(ordered, 0.99):.3f} ms, "
            f"max={ordered[-1]:.3f} ms"
        )

    print(f"Large host gaps over {args.threshold_ms:g} ms: {len(large_gaps)}")
    for t_s, gap_ms in large_gaps[: args.max_gap_lines]:
        print(f"  t={t_s:9.3f}s  gap={gap_ms:9.3f} ms")
    if len(large_gaps) > args.max_gap_lines:
        print(f"  ... {len(large_gaps) - args.max_gap_lines} additional gaps not printed")

    if len(large_gaps) >= 2:
        spacings = [
            large_gaps[index + 1][0] - large_gaps[index][0]
            for index in range(len(large_gaps) - 1)
        ]
        print(
            "Spacing between large host gaps: "
            f"mean={statistics.fmean(spacings):.3f}s, "
            f"min={min(spacings):.3f}s, max={max(spacings):.3f}s"
        )

    print(
        "Interpretation limit: host gaps include USB/eZ-FET/OS buffering and are "
        "not direct ADC timestamps."
    )

    passed = bool(
        args.sampling_rate <= capacity
        and abs(rate_error) <= args.tolerance_percent
        and not reader.stats.corruption_detected
        and sample_count > 0
    )
    print("Host stream validation: " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
