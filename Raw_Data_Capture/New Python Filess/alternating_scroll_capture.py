"""Capture one seated near-range take with alternating left/right event labels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from raw_serial_capture import capture_stream, positive_float, safe_label, sampling_rate_tag
from timed_pilot_capture import PILOT_ACTIONS, PILOT_SPEEDS, SPEED_PROFILES, positive_int


LEFT_CLASS = "left_horizontal_scroll"
RIGHT_CLASS = "right_horizontal_scroll"


def build_alternating_schedule(
    *,
    speed: str,
    pairs: int,
    initial_idle_s: float,
    repeat_period_s: float,
    action_window_s: float,
) -> list[dict[str, Any]]:
    motion_duration_s = float(SPEED_PROFILES[speed]["motion_duration_s"])
    if motion_duration_s >= action_window_s:
        raise ValueError("motion duration must be shorter than the action window")
    events: list[dict[str, Any]] = []
    for index in range(pairs * 2):
        gesture_class = LEFT_CLASS if index % 2 == 0 else RIGHT_CLASS
        start_s = initial_idle_s + index * repeat_period_s
        events.append(
            {
                "event_id": f"event{index + 1:02d}",
                "repetition": index + 1,
                "pair": index // 2 + 1,
                "label": gesture_class,
                "gesture_class": gesture_class,
                "speed": speed,
                "direction": PILOT_ACTIONS[gesture_class]["direction"],
                "motion_duration_s": motion_duration_s,
                "start_s": start_s,
                "motion_end_s": start_s + motion_duration_s,
                "end_s": start_s + action_window_s,
            }
        )
    return events


def append_manifest(root: Path, metadata: dict[str, Any]) -> Path:
    path = root / "model-pilot" / "direction_validation_manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = Path(metadata["csv_path"])
    metadata_path = Path(metadata["metadata_path"])
    try:
        csv_value = str(csv_path.relative_to(root))
        metadata_value = str(metadata_path.relative_to(root))
    except ValueError:
        csv_value = str(csv_path.resolve())
        metadata_value = str(metadata_path.resolve())
    entry = {
        "schema_version": 1,
        "data_path": csv_value,
        "metadata_path": metadata_value,
        "data_sha256": metadata["data_sha256"],
        "subject_id": metadata["subject_id"],
        "session_id": metadata["session_id"],
        "capture_purpose": metadata["capture_purpose"],
        "speed": metadata["speed"],
        "distance": metadata["distance"],
        "event_count": len(metadata.get("event_markers", [])),
        "capture_validation_passed": metadata["host_transport_validation_passed"],
    }
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=positive_float, default=2000.0)
    parser.add_argument("--speed", choices=PILOT_SPEEDS, default="normal")
    parser.add_argument("--distance", choices=("near",), default="near")
    parser.add_argument("--subject", default="subject01")
    parser.add_argument("--session", default="directioncheck01")
    parser.add_argument("--pairs", type=positive_int, default=10)
    parser.add_argument("--initial-idle", type=positive_float, default=3.0)
    parser.add_argument("--repeat-period", type=positive_float, default=5.0)
    parser.add_argument("--final-idle", type=positive_float, default=3.0)
    parser.add_argument("--action-window", type=positive_float, default=1.0)
    parser.add_argument("--countdown", type=positive_int, default=3)
    parser.add_argument("--out", default="dataset")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sampling_rate != 2000:
        print("Error: the direction check is fixed at 2000 samples/s.", file=sys.stderr)
        return 2
    if args.action_window >= args.repeat_period:
        print("Error: action window must be shorter than repeat period.", file=sys.stderr)
        return 2
    try:
        events = build_alternating_schedule(
            speed=args.speed,
            pairs=args.pairs,
            initial_idle_s=args.initial_idle,
            repeat_period_s=args.repeat_period,
            action_window_s=args.action_window,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    subject = safe_label(args.subject).lower()
    session = safe_label(args.session).lower()
    duration_s = events[-1]["end_s"] + args.final_idle
    root = Path(args.out)
    out_dir = (
        root
        / "model-pilot"
        / "direction-validation"
        / "raw"
        / sampling_rate_tag(args.sampling_rate)
        / subject
        / session
        / args.speed
        / args.distance
    )
    label = f"{subject}_{session}_alt_scroll_{args.speed}_{args.distance}"

    print("Seated alternating scroll direction check")
    print(f"Speed: {args.speed}; distance: {args.distance}; events: {len(events)}")
    print(f"Duration: {duration_s:g} s")
    print("Begin with the hand at the RIGHT endpoint.")
    print("Do not reset between events: remain at the endpoint until the next START.")
    print("Odd events move RIGHT-TO-LEFT; even events move LEFT-TO-RIGHT.")
    print("The terminal prints the required class and direction at every START.")
    for event in events:
        print(
            f"  {event['event_id']} pair {event['pair']:02d}: "
            f"{event['start_s']:.1f}-{event['end_s']:.1f} s, "
            f"{event['gesture_class']} ({event['direction']})"
        )

    input(f"Press Enter to start the {args.countdown}-second countdown...")
    for remaining in range(args.countdown, 0, -1):
        print(f"  Capture starts in {remaining}...")
        time.sleep(1)
    print("\aCAPTURE START")

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    try:
        metadata = capture_stream(
            serial_port,
            port_name=args.port,
            baud=args.baud,
            sampling_rate_hz=args.sampling_rate,
            duration_s=duration_s,
            label=label,
            out_dir=out_dir,
            event_schedule=events,
            extra_metadata={
                "capture_purpose": "alternating-scroll-direction-validation",
                "subject_id": subject,
                "session_id": session,
                "condition": "alternating_horizontal_scroll",
                "gesture_class": "mixed_left_right_horizontal_scroll",
                "gesture_direction": "alternating",
                "speed": args.speed,
                "distance": args.distance,
                "posture": "seated",
                "hand_height": "driver-seat-operating-height",
                "pair_count": args.pairs,
                "event_count_expected": len(events),
                "pre_capture_countdown_s": args.countdown,
                "repeat_period_s": args.repeat_period,
                "action_window_s": args.action_window,
                "motion_duration_s": SPEED_PROFILES[args.speed]["motion_duration_s"],
                "action_instruction": (
                    "Begin at the right endpoint and alternate right-to-left then "
                    "left-to-right, holding each endpoint until the next START."
                ),
            },
        )
    finally:
        serial_port.close()

    print(f"CSV: {metadata['csv_path']}")
    print(f"Metadata: {metadata['metadata_path']}")
    print(f"Manifest: {append_manifest(root, metadata)}")
    passed = metadata["host_transport_validation_passed"]
    print(f"Capture validation: {'PASS' if passed else 'FAIL'}")
    if not passed:
        print("Do not use this take; repeat it before continuing.", file=sys.stderr)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
