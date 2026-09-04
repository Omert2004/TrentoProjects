"""Capture one 2 kHz model-pilot take with reproducible gesture markers.

The model target has four classes.  ``speed`` is deliberately recorded as a
separate subset/attribute rather than being joined to the class name.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from raw_serial_capture import (
    FILTER_DISTANCES,
    capture_stream,
    positive_float,
    safe_label,
    sampling_rate_tag,
)


PILOT_SPEEDS = ("slow", "normal", "fast")
SPEED_PROFILES = {
    "slow": {"motion_duration_s": 0.75},
    "normal": {"motion_duration_s": 0.50},
    "fast": {"motion_duration_s": 0.25},
}

PILOT_ACTIONS = {
    "clicking_hand": {
        "direction": "open-to-fist",
        "instruction": (
            "Begin with an open hand. At START, close it once into a fist in "
            "about {motion_duration_s:g} s, then keep the fist stationary until "
            "STOP. Reopen and reset only after STOP."
        ),
    },
    "left_horizontal_scroll": {
        "direction": "right-to-left",
        "instruction": (
            "Begin at the right-hand position. At START, scroll once to the "
            "left in about {motion_duration_s:g} s, then hold until STOP. Reset "
            "only after STOP."
        ),
    },
    "right_horizontal_scroll": {
        "direction": "left-to-right",
        "instruction": (
            "Begin at the left-hand position. At START, scroll once to the "
            "right in about {motion_duration_s:g} s, then hold until STOP. Reset "
            "only after STOP."
        ),
    },
}
PILOT_CLASSES = (
    "clicking_hand",
    "left_horizontal_scroll",
    "right_horizontal_scroll",
    "empty",
)


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def build_event_schedule(
    gesture_class: str,
    *,
    speed: str,
    repetitions: int,
    initial_idle_s: float,
    repeat_period_s: float,
    action_window_s: float,
) -> list[dict[str, Any]]:
    if gesture_class not in PILOT_ACTIONS:
        raise ValueError(f"class {gesture_class!r} is not a timed pilot action")
    if speed not in SPEED_PROFILES:
        raise ValueError(f"unknown speed {speed!r}")
    direction = str(PILOT_ACTIONS[gesture_class]["direction"])
    motion_duration_s = float(SPEED_PROFILES[speed]["motion_duration_s"])
    if motion_duration_s >= action_window_s:
        raise ValueError("motion duration must be shorter than the action window")
    events: list[dict[str, Any]] = []
    for index in range(repetitions):
        start_s = initial_idle_s + index * repeat_period_s
        events.append(
            {
                "event_id": f"event{index + 1:02d}",
                "repetition": index + 1,
                "label": gesture_class,
                "gesture_class": gesture_class,
                "speed": speed,
                "direction": direction,
                "motion_duration_s": motion_duration_s,
                "start_s": start_s,
                "motion_end_s": start_s + motion_duration_s,
                "end_s": start_s + action_window_s,
            }
        )
    return events


def append_manifest(root: Path, metadata: dict[str, Any]) -> Path:
    path = root / "model-pilot" / "capture_manifest.jsonl"
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
        "schema_version": 2,
        "data_path": csv_value,
        "metadata_path": metadata_value,
        "data_sha256": metadata["data_sha256"],
        "subject_id": metadata["subject_id"],
        "session_id": metadata["session_id"],
        "gesture_class": metadata["gesture_class"],
        "speed": metadata["speed"],
        "distance": metadata["distance"],
        "sampling_rate_hz": metadata["configured_sampling_rate_hz"],
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
    parser.add_argument(
        "--gesture-class",
        "--condition",
        dest="gesture_class",
        choices=PILOT_CLASSES,
        required=True,
        help="Four-class model target; --condition is retained as a CLI alias.",
    )
    parser.add_argument("--speed", choices=PILOT_SPEEDS, required=True)
    parser.add_argument("--distance", choices=FILTER_DISTANCES)
    parser.add_argument("--subject", default="subject01")
    parser.add_argument("--session", required=True)
    parser.add_argument("--repetitions", type=positive_int, default=5)
    parser.add_argument("--initial-idle", type=positive_float, default=3.0)
    parser.add_argument("--repeat-period", type=positive_float, default=5.0)
    parser.add_argument("--final-idle", type=positive_float, default=3.0)
    parser.add_argument(
        "--action-window",
        "--action-duration",
        dest="action_window",
        type=positive_float,
        default=1.0,
        help="Marked gesture window in seconds (default: 1.0).",
    )
    parser.add_argument("--static-duration", type=positive_float, default=20.0)
    parser.add_argument("--marker-guard", type=positive_float, default=0.15)
    parser.add_argument(
        "--countdown",
        type=positive_int,
        default=3,
        help="Preparation countdown after Enter and before capture starts (default: 3).",
    )
    parser.add_argument("--out", default="dataset")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sampling_rate != 2000:
        print("Error: the model pilot is fixed at 2000 samples/s.", file=sys.stderr)
        return 2
    if args.gesture_class == "empty":
        if args.distance is not None:
            print("Error: empty must not specify --distance.", file=sys.stderr)
            return 2
    elif args.distance is None:
        print("Error: nonempty conditions require --distance.", file=sys.stderr)
        return 2

    subject = safe_label(args.subject).lower()
    session = safe_label(args.session).lower()
    distance = args.distance or "na"
    timed_action = args.gesture_class in PILOT_ACTIONS
    events: list[dict[str, Any]] = []
    if timed_action:
        action_window = float(args.action_window)
        if action_window >= args.repeat_period:
            print("Error: action window must be shorter than repeat period.", file=sys.stderr)
            return 2
        try:
            events = build_event_schedule(
                args.gesture_class,
                speed=args.speed,
                repetitions=args.repetitions,
                initial_idle_s=args.initial_idle,
                repeat_period_s=args.repeat_period,
                action_window_s=action_window,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        duration_s = events[-1]["end_s"] + args.final_idle
    else:
        action_window = None
        duration_s = args.static_duration

    root = Path(args.out)
    out_dir = (
        root
        / "model-pilot"
        / "raw"
        / sampling_rate_tag(args.sampling_rate)
        / subject
        / session
        / args.gesture_class
        / args.speed
        / distance
    )
    label = f"{subject}_{session}_{args.gesture_class}_{args.speed}_{distance}"
    print(
        f"Class: {args.gesture_class}; speed subset: {args.speed}; "
        f"distance: {distance}; duration: {duration_s:g} s"
    )
    if events:
        instruction = str(PILOT_ACTIONS[args.gesture_class]["instruction"]).format(
            motion_duration_s=SPEED_PROFILES[args.speed]["motion_duration_s"]
        )
        print(f"Action: {instruction}")
        for event in events:
            print(
                f"  repetition {event['repetition']}: {event['start_s']:.1f}-"
                f"{event['end_s']:.1f} s, {event['direction']}"
            )
        print("The terminal bell marks START and STOP for every repetition.")
    input(
        f"Press Enter to start the {args.countdown}-second preparation countdown..."
    )
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
                "capture_purpose": "model-pilot",
                "subject_id": subject,
                "session_id": session,
                "condition": args.gesture_class,
                "distance": args.distance,
                "gesture_class": args.gesture_class,
                "speed": args.speed,
                "gesture_direction": (
                    PILOT_ACTIONS[args.gesture_class]["direction"] if timed_action else None
                ),
                "action_instruction": (
                    instruction if timed_action else "Remain outside the radar range."
                ),
                "marker_guard_s": args.marker_guard,
                "pre_capture_countdown_s": args.countdown,
                "repeat_period_s": args.repeat_period if timed_action else None,
                "action_window_s": action_window,
                "motion_duration_s": (
                    SPEED_PROFILES[args.speed]["motion_duration_s"]
                    if timed_action
                    else None
                ),
            },
        )
    finally:
        serial_port.close()

    print(f"CSV: {metadata['csv_path']}")
    print(f"Metadata: {metadata['metadata_path']}")
    manifest_path = append_manifest(root, metadata)
    print(f"Manifest: {manifest_path}")
    passed = metadata["host_transport_validation_passed"]
    print(f"Capture validation: {'PASS' if passed else 'FAIL'}")
    if not passed:
        print("Do not use this take; repeat it before continuing.", file=sys.stderr)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())