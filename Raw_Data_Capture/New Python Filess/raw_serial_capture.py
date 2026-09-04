"""Capture the 2 kHz packetized raw I/Q stream to validated CSV.

The companion ``.metadata.json`` records the requested sampling rate, observed
receive rate, UART capacity, CRC/sequence integrity, and the MCU's cumulative
ring-drop counter. CSV ``sample_idx`` values come directly from the MCU.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
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


CAPTURE_PURPOSES = ("general", "filter-experiment")
FILTER_CONDITIONS = (
    "empty_scene",
    "stationary_hand",
    "slow_movement",
    "normal_movement",
    "fast_movement",
)
FILTER_DISTANCES = ("near", "mid", "far")
MOVEMENT_SPEEDS = {
    "slow_movement": "slow",
    "normal_movement": "normal",
    "fast_movement": "fast",
}


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


def sampling_rate_tag(sampling_rate_hz: float) -> str:
    value = float(sampling_rate_hz)
    if value.is_integer():
        return f"fs{int(value)}"
    return f"fs{str(value).replace('.', 'p')}"


def filter_capture_directory(
    root: Path,
    *,
    sampling_rate_hz: float,
    condition: str,
    distance: str | None,
) -> Path:
    distance_tag = distance if distance is not None else "na"
    return (
        root
        / "filter-experiments"
        / "raw"
        / sampling_rate_tag(sampling_rate_hz)
        / condition
        / distance_tag
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def append_filter_manifest(root: Path, metadata: dict[str, Any]) -> Path:
    manifest_path = root / "filter-experiments" / "capture_manifest.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = Path(metadata["csv_path"])
    metadata_path = Path(metadata["metadata_path"])
    try:
        stored_csv_path = str(csv_path.relative_to(root))
        stored_metadata_path = str(metadata_path.relative_to(root))
    except ValueError:
        stored_csv_path = str(csv_path.resolve())
        stored_metadata_path = str(metadata_path.resolve())
    entry = {
        "schema_version": 1,
        "started_utc": metadata["started_utc"],
        "data_path": stored_csv_path,
        "metadata_path": stored_metadata_path,
        "data_sha256": metadata["data_sha256"],
        "experiment_id": metadata["experiment_id"],
        "sampling_rate_hz": metadata["configured_sampling_rate_hz"],
        "condition": metadata["condition"],
        "distance": metadata["distance"],
        "speed": metadata["speed"],
        "direction": metadata["direction"],
        "subject_id": metadata["subject_id"],
        "received_samples": metadata["received_samples"],
        "capture_validation_passed": metadata["host_transport_validation_passed"],
    }
    with manifest_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return manifest_path


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
    extra_metadata: dict[str, Any] | None = None,
    event_schedule: list[dict[str, Any]] | None = None,
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
    first_device_sample_index: int | None = None
    last_device_sample_index: int | None = None
    event_records = [dict(event) for event in (event_schedule or [])]
    notifications: list[tuple[float, str, int]] = []
    for event_index, event in enumerate(event_records):
        notifications.append((float(event["start_s"]), "start", event_index))
        notifications.append((float(event["end_s"]), "end", event_index))
    notifications.sort(key=lambda item: item[0])
    notification_index = 0

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
                    if first_device_sample_index is None:
                        first_device_sample_index = device_sample_index
                    last_device_sample_index = device_sample_index
                    writer.writerow([device_sample_index, 0, ifi, ifq])
                    samples += 1

                now_ns = time.perf_counter_ns()
                elapsed = (now_ns - started_ns) / 1_000_000_000
                while (
                    notification_index < len(notifications)
                    and elapsed >= notifications[notification_index][0]
                ):
                    _scheduled_s, boundary, event_index = notifications[notification_index]
                    event = event_records[event_index]
                    event[f"actual_{boundary}_host_elapsed_s"] = elapsed
                    if boundary == "start":
                        print(
                            f"\aSTART repetition {event.get('repetition', event_index + 1)}: "
                            f"{event.get('label', 'action')} "
                            f"{event.get('direction', '')}".rstrip()
                        )
                    else:
                        print(
                            f"\aSTOP repetition {event.get('repetition', event_index + 1)}"
                        )
                    notification_index += 1
                if now_ns >= next_progress_ns:
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
        "first_device_sample_index": first_device_sample_index,
        "last_device_sample_index": last_device_sample_index,
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

    if event_records:
        for event in event_records:
            start_offset = int(round(float(event["start_s"]) * sampling_rate_hz))
            end_offset = int(round(float(event["end_s"]) * sampling_rate_hz))
            event["scheduled_start_sample_offset"] = start_offset
            event["scheduled_end_sample_offset_exclusive"] = end_offset
            if first_device_sample_index is not None:
                event["scheduled_start_device_sample_index"] = (
                    first_device_sample_index + start_offset
                ) & 0xFFFFFFFF
                event["scheduled_end_device_sample_index_exclusive"] = (
                    first_device_sample_index + end_offset
                ) & 0xFFFFFFFF
        metadata["event_markers"] = event_records
        metadata["event_marker_timebase"] = "host schedule mapped from first captured device sample"

    if extra_metadata:
        metadata.update(extra_metadata)

    metadata["data_sha256"] = sha256_file(csv_path)

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
        "--capture-purpose",
        choices=CAPTURE_PURPOSES,
        default="general",
    )
    parser.add_argument("--condition", choices=FILTER_CONDITIONS)
    parser.add_argument("--distance", choices=FILTER_DISTANCES)
    parser.add_argument("--subject", default="subject01")
    parser.add_argument("--direction")
    parser.add_argument("--experiment-id", default="filter-characterization-v1")
    parser.add_argument("--notes")
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

    out_root = Path(args.out)
    extra_metadata: dict[str, Any] = {"capture_purpose": args.capture_purpose}
    label = args.label
    out_dir = out_root
    if args.capture_purpose == "filter-experiment":
        if args.sampling_rate != 2000:
            print(
                "Error: this filter experiment is fixed at --sampling-rate 2000.",
                file=sys.stderr,
            )
            return 2
        if args.condition is None:
            print("Error: filter-experiment requires --condition.", file=sys.stderr)
            return 2
        if args.condition == "empty_scene":
            if args.distance is not None:
                print("Error: empty_scene must not specify --distance.", file=sys.stderr)
                return 2
        elif args.distance is None:
            print(
                "Error: nonempty filter conditions require --distance near, mid, or far.",
                file=sys.stderr,
            )
            return 2
        if args.condition in {"empty_scene", "stationary_hand"} and args.direction:
            print(
                f"Error: {args.condition} must not specify --direction.",
                file=sys.stderr,
            )
            return 2

        subject = safe_label(args.subject).lower()
        direction = safe_label(args.direction).lower() if args.direction else None
        experiment_id = safe_label(args.experiment_id).lower()
        speed = MOVEMENT_SPEEDS.get(args.condition)
        label_parts = [subject, args.condition]
        if args.distance:
            label_parts.append(args.distance)
        if direction:
            label_parts.append(direction)
        label = "_".join(label_parts)
        out_dir = filter_capture_directory(
            out_root,
            sampling_rate_hz=args.sampling_rate,
            condition=args.condition,
            distance=args.distance,
        )
        extra_metadata.update(
            {
                "experiment_id": experiment_id,
                "filter_stage": "unfiltered_raw_input",
                "condition": args.condition,
                "distance": args.distance,
                "speed": speed,
                "direction": direction,
                "subject_id": subject,
                "notes": args.notes,
            }
        )

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
            label=label,
            out_dir=out_dir,
            rate_tolerance_percent=args.rate_tolerance_percent,
            extra_metadata=extra_metadata,
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
    if args.capture_purpose == "filter-experiment":
        manifest_path = append_filter_manifest(out_root, metadata)
        print(f"Manifest: {manifest_path}")
    for warning in metadata["warnings"]:
        print(f"Warning: {warning}")

    if metadata["host_transport_validation_passed"]:
        print("Capture validation: PASS (CRC, device sequences, drops, and rate checked).")
        return 0

    print("Capture validation: FAIL; do not use this capture as a clean dataset.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
