"""Capture the 2 kHz packetized raw I/Q stream to validated CSV.

The companion ``.metadata.json`` records the requested sampling rate, observed
receive rate, UART capacity, CRC/sequence integrity, and the MCU's cumulative
ring-drop counter. CSV ``sample_idx`` values come directly from the MCU.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any

from raw_protocol import (
    MAX_SAMPLES_PER_PACKET,
    RawPacketReader,
    packet_size,
    serial_capacity_samples_per_second,
)


def positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def nonnegative_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._")
    return cleaned or "capture"


def make_output_paths(out_dir: Path, label: str) -> tuple[Path, Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    stem = f"{safe_label(label)}_{stamp}"
    return out_dir / f"{stem}.csv", out_dir / f"{stem}.metadata.json"


def capture_stream(
    serial_port: Any,
    *,
    port_name: str,
    baud: int,
    sampling_rate_hz: float,
    duration_s: float | None,
    label: str,
    out_dir: Path,
    rate_tolerance_percent: float = 2.0,
    reset_input: bool = True,
) -> dict[str, Any]:
    """Capture one stream and return the metadata written beside the CSV."""

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path, metadata_path = make_output_paths(out_dir, label)

    if reset_input:
        serial_port.reset_input_buffer()

    # One full packet per read keeps short-capture boundary error below one
    # 32-sample block while remaining efficient.
    reader = RawPacketReader(
        serial_port,
        read_size=packet_size(MAX_SAMPLES_PER_PACKET),
    )
    started_utc = datetime.now(timezone.utc)
    started_ns = time.perf_counter_ns()
    deadline_ns = (
        started_ns + int(duration_s * 1_000_000_000)
        if duration_s is not None
        else None
    )

    samples = 0
    interrupted = False
    next_progress_ns = started_ns + 1_000_000_000

    with csv_path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sample_idx", "segment", "I", "Q"])

        try:
            while deadline_ns is None or time.perf_counter_ns() < deadline_ns:
                packet = reader.read_packet()
                if packet is None:
                    continue

                for offset, (ifi, ifq) in enumerate(packet.samples):
                    device_sample_index = (
                        packet.first_sample_index + offset
                    ) & 0xFFFFFFFF
                    writer.writerow([device_sample_index, 0, ifi, ifq])
                    samples += 1

                now_ns = time.perf_counter_ns()
                if now_ns >= next_progress_ns:
                    elapsed = (now_ns - started_ns) / 1_000_000_000
                    print(
                        f"  {samples} samples, {samples / elapsed:.1f} samples/s, "
                        f"parser resyncs={reader.stats.resync_events}"
                    )
                    next_progress_ns = now_ns + 1_000_000_000
        except KeyboardInterrupt:
            interrupted = True
            print("\nCapture stopped by user.")

    ended_ns = time.perf_counter_ns()
    ended_utc = datetime.now(timezone.utc)
    elapsed_s = max((ended_ns - started_ns) / 1_000_000_000, 1e-12)
    observed_rate = samples / elapsed_s
    capacity = serial_capacity_samples_per_second(baud)
    utilization = sampling_rate_hz / capacity
    rate_error_percent = 100.0 * (observed_rate - sampling_rate_hz) / sampling_rate_hz
    rate_within_tolerance = abs(rate_error_percent) <= rate_tolerance_percent
    target_exceeds_link = sampling_rate_hz > capacity

    expected_samples = (
        int(round(duration_s * sampling_rate_hz))
        if duration_s is not None
        else None
    )

    warnings: list[str] = []
    if target_exceeds_link:
        warnings.append(
            f"Requested sampling rate {sampling_rate_hz:g} Hz exceeds the "
            f"{capacity:.1f} sample/s packetized wire ceiling."
        )
    if reader.stats.corruption_detected:
        warnings.append("Parser detected post-sync corruption or lost alignment.")
    if not rate_within_tolerance:
        warnings.append(
            f"Observed receive rate differs from the configured sampling rate by "
            f"{rate_error_percent:+.2f}%."
        )
    if (
        reader.stats.first_reported_drop_count
        and not reader.stats.reported_drop_increase
    ):
        warnings.append(
            "The MCU reported drops before this capture began, but its drop "
            "counter did not increase during the captured packet sequence."
        )

    host_validation_passed = bool(
        not target_exceeds_link
        and not reader.stats.corruption_detected
        and rate_within_tolerance
        and samples > 0
    )

    metadata: dict[str, Any] = {
        "schema_version": 2,
        "capture_csv": csv_path.name,
        "started_utc": started_utc.isoformat(),
        "ended_utc": ended_utc.isoformat(),
        "interrupted_by_user": interrupted,
        "label": safe_label(label),
        "serial_port": port_name,
        "baud": baud,
        "configured_sampling_rate_hz": sampling_rate_hz,
        "requested_duration_s": duration_s,
        "actual_host_elapsed_s": elapsed_s,
        "received_samples": samples,
        "expected_samples_from_host_window": expected_samples,
        "observed_receive_rate_hz": observed_rate,
        "receive_rate_error_percent": rate_error_percent,
        "rate_tolerance_percent": rate_tolerance_percent,
        "rate_within_tolerance": rate_within_tolerance,
        "protocol": "raw-packet-v1",
        "uart_full_packet_bytes": packet_size(MAX_SAMPLES_PER_PACKET),
        "uart_samples_per_full_packet": MAX_SAMPLES_PER_PACKET,
        "uart_wire_format": "8-N-1",
        "uart_theoretical_sample_ceiling_hz": capacity,
        "requested_uart_utilization": utilization,
        "target_exceeds_uart_capacity": target_exceeds_link,
        "adc_code_range": [0, 4095],
        "parser": reader.stats.to_dict(),
        "host_transport_validation_passed": host_validation_passed,
        "device_sequence_available": True,
        "device_drop_detection_available": True,
        "scientific_sample_continuity_proven": host_validation_passed,
        "warnings": warnings,
    }

    with metadata_path.open("x", encoding="utf-8") as stream:
        json.dump(metadata, stream, indent=2)
        stream.write("\n")

    metadata["csv_path"] = str(csv_path)
    metadata["metadata_path"] = str(metadata_path)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--sampling-rate",
        type=positive_float,
        required=True,
        help="Firmware sampling rate in Hz; required to prevent stale metadata.",
    )
    parser.add_argument(
        "--duration",
        type=positive_float,
        default=None,
        help="Capture duration in seconds; omit to run until Ctrl+C.",
    )
    parser.add_argument("--label", default="capture")
    parser.add_argument("--out", default="captures", help="Output directory")
    parser.add_argument(
        "--rate-tolerance-percent",
        type=nonnegative_float,
        default=2.0,
        help="Allowed host receive-rate error before validation fails (default: 2).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud <= 0:
        print("Error: --baud must be positive.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
        return 2

    print(f"Connecting to {args.port} @ {args.baud} baud...")
    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    try:
        metadata = capture_stream(
            serial_port,
            port_name=args.port,
            baud=args.baud,
            sampling_rate_hz=args.sampling_rate,
            duration_s=args.duration,
            label=args.label,
            out_dir=Path(args.out),
            rate_tolerance_percent=args.rate_tolerance_percent,
        )
    finally:
        serial_port.close()

    print(
        f"\nCaptured {metadata['received_samples']} samples in "
        f"{metadata['actual_host_elapsed_s']:.3f}s "
        f"({metadata['observed_receive_rate_hz']:.1f} samples/s)."
    )
    print(f"CSV: {metadata['csv_path']}")
    print(f"Metadata: {metadata['metadata_path']}")
    for warning in metadata["warnings"]:
        print(f"Warning: {warning}")

    if metadata["host_transport_validation_passed"]:
        print("Capture validation: PASS (CRC, device sequences, drops, and rate checked).")
        return 0

    print("Capture validation: FAIL; do not use this capture as a clean dataset.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
