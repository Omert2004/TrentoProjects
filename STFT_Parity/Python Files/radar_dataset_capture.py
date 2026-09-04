"""Validated dataset capture for the CRC-protected AI_Phase stream.

The data file contains the original, unmasked 256-bin on-chip columns. A
matching .metadata.json records parser integrity statistics, device continuity,
MCU drops, measured rate, diagnostic reports, and a SHA-256 of the data file.
A failed capture is saved for diagnosis but exits with status 2.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Optional

from stft_data import metadata_path_for, sha256_file
from stft_protocol import ColumnPacket, ProfilePacket, RatePacket, StftFrameReader

GESTURE_CLASSES = ("no_movement", "horizontal_slide", "closed_fist")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET_ROOT = PROJECT_ROOT / "dataset"
FFT_SIZE = 256
FFT_HOP = 128
SAMPLING_RATE_HZ = 4000.0
STFT_SEGMENTS = 15


def safe_tag(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9-]+", "-", text.strip()).strip("-")
    if not value:
        raise ValueError("tag must contain at least one letter or digit")
    return value.lower()


def next_session_path(root: Path, gesture: str, distance: Optional[str], angle: Optional[int]) -> Path:
    class_dir = root / safe_tag(gesture)
    class_dir.mkdir(parents=True, exist_ok=True)
    parts = [safe_tag(gesture)]
    if distance:
        parts.append(safe_tag(distance))
    if angle:
        parts.append(f"{'neg' if angle < 0 else ''}{abs(angle)}deg")
    base = "_".join(parts)
    used = []
    for path in class_dir.glob(f"{base}_session*.txt"):
        match = re.search(r"_session(\d+)$", path.stem)
        if match:
            used.append(int(match.group(1)))
    return class_dir / f"{base}_session{max(used, default=0) + 1:03d}.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=float, default=SAMPLING_RATE_HZ)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--duration", type=float, default=None, help="seconds; omit for Ctrl+C")
    duration.add_argument("--duration-min", type=float, default=None)
    parser.add_argument("--gesture-class", choices=GESTURE_CLASSES)
    parser.add_argument("--distance")
    parser.add_argument("--angle", type=int)
    parser.add_argument("--speed")
    parser.add_argument(
        "--clutter-cancel",
        type=int,
        choices=(0, 1),
        default=0,
        help="Must match ENABLE_CLUTTER_CANCEL in STFT.h (default: 0).",
    )
    parser.add_argument("--repeat-interval", type=float)
    parser.add_argument("--log-file")
    parser.add_argument("--out", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--rate-tolerance-percent", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud <= 0 or args.sampling_rate <= 0 or args.rate_tolerance_percent < 0:
        print("Error: baud/sampling rate must be positive and tolerance nonnegative.", file=sys.stderr)
        return 2
    duration = args.duration if args.duration is not None else (
        args.duration_min * 60.0 if args.duration_min is not None else None
    )
    if duration is not None and duration <= 0:
        print("Error: duration must be positive.", file=sys.stderr)
        return 2
    if not args.log_file and not args.gesture_class:
        print("Error: use --gesture-class or --log-file.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    try:
        output_path = Path(args.log_file) if args.log_file else next_session_path(
            Path(args.out), args.gesture_class, args.distance, args.angle
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = metadata_path_for(output_path)
    if output_path.exists() or meta_path.exists():
        print(f"Error: refusing to overwrite {output_path} or its metadata.", file=sys.stderr)
        return 2

    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    serial_port.reset_input_buffer()
    reader = StftFrameReader(serial_port)
    columns: list[ColumnPacket] = []
    rate_reports: list[dict] = []
    profile_reports: list[dict] = []
    repeat_markers: list[dict] = []
    started_wall = datetime.now(timezone.utc)
    started = time.perf_counter()
    next_repeat = started + args.repeat_interval if args.repeat_interval else None
    print(f"Capturing validated STFT columns from {args.port} @ {args.baud}.")
    print(f"Output: {output_path}. Press Ctrl+C to stop.")

    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        try:
            while duration is None or time.perf_counter() - started < duration:
                frame = reader.read_frame()
                now = time.perf_counter()
                if isinstance(frame, ColumnPacket):
                    columns.append(frame)
                    stream.write(" ".join(str(value) for value in frame.values) + "\n")
                    if len(columns) % 64 == 0:
                        stream.flush()
                        print(
                            f"{len(columns):6d} columns, "
                            f"seq={frame.column_sequence}, sample={frame.first_new_sample_index}, "
                            f"drops={frame.cumulative_drop_count}"
                        )
                elif isinstance(frame, RatePacket):
                    rate_reports.append({
                        "host_elapsed_seconds": now - started,
                        "report_sequence": frame.report_sequence,
                        "accepted_samples": frame.accepted_samples,
                        "cumulative_drop_count": frame.cumulative_drop_count,
                    })
                elif isinstance(frame, ProfilePacket):
                    profile_reports.append({
                        "host_elapsed_seconds": now - started,
                        "report_sequence": frame.report_sequence,
                        "hop_count": frame.hop_count,
                        "stft_ticks": frame.stft_ticks,
                        "dma_wait_ticks": frame.dma_wait_ticks,
                    })

                if next_repeat is not None and now >= next_repeat:
                    repeat_markers.append({
                        "host_elapsed_seconds": now - started,
                        "column_index": len(columns),
                    })
                    print("\aREPEAT gesture now")
                    while next_repeat <= now:
                        next_repeat += args.repeat_interval
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            stream.flush()
            serial_port.close()

    ended = time.perf_counter()
    duration_seconds = ended - started
    if len(columns) > 1:
        column_span_seconds = (columns[-1].host_time_ns - columns[0].host_time_ns) / 1e9
        observed_rate = (len(columns) - 1) / column_span_seconds if column_span_seconds > 0 else 0.0
    else:
        observed_rate = 0.0
    target_rate = args.sampling_rate / FFT_HOP
    rate_error_pct = 100.0 * (observed_rate - target_rate) / target_rate
    stats = reader.stats
    no_mcu_drops = (
        stats.first_reported_drop_count == 0
        and stats.last_reported_drop_count == 0
        and stats.reported_drop_increase == 0
    )
    passed = (
        len(columns) > 1
        and abs(rate_error_pct) <= args.rate_tolerance_percent
        and not stats.corruption_detected
        and no_mcu_drops
    )

    metadata = {
        "schema_version": 3,
        "protocol": "AI_Phase STFT v1 D0/D2/D3 CRC16-CCITT-FALSE",
        "created_utc": started_wall.isoformat().replace("+00:00", "Z"),
        "gesture_class": args.gesture_class,
        "distance": args.distance,
        "angle_degrees": args.angle,
        "speed": args.speed,
        "port": args.port,
        "baud": args.baud,
        "configured_sampling_rate_hz": args.sampling_rate,
        "fft_size": FFT_SIZE,
        "fft_hop": FFT_HOP,
        "stft_segments": STFT_SEGMENTS,
        "clutter_cancel_enabled": bool(args.clutter_cancel),
        "columns_captured": len(columns),
        "duration_seconds": duration_seconds,
        "target_column_rate_hz": target_rate,
        "observed_column_rate_hz": observed_rate,
        "rate_error_percent": rate_error_pct,
        "rate_tolerance_percent": args.rate_tolerance_percent,
        "first_column_sequence": columns[0].column_sequence if columns else None,
        "last_column_sequence": columns[-1].column_sequence if columns else None,
        "first_new_sample_index": columns[0].first_new_sample_index if columns else None,
        "last_new_sample_index": columns[-1].first_new_sample_index if columns else None,
        "parser_statistics": stats.to_dict(),
        "rate_reports": rate_reports,
        "profile_reports": profile_reports,
        "repeat_markers": repeat_markers,
        "host_transport_validation_passed": passed,
        "scientific_sample_continuity_proven": passed,
        "on_chip_vs_python_stft_parity_verified": False,
        "data_sha256": sha256_file(output_path),
    }
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print(f"\nColumns: {len(columns)}; observed rate: {observed_rate:.3f} Hz ({rate_error_pct:+.2f}%)")
    print(f"Parser statistics: {stats.to_dict()}")
    print(f"Capture validation: {'PASS' if passed else 'FAIL'}")
    print(f"Data: {output_path}")
    print(f"Metadata: {meta_path}")
    if not passed:
        print("Do not use this capture as a clean dataset.", file=sys.stderr)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
