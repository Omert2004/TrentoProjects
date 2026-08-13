"""
radar_dataset_capture.py

Text-mode dataset-collection tool for the MSP430FR5994 firmware (AI_Phase).
Same frame parsing / stats as STFT_check.py (prints each column's DC peak
and motion peak, no plot window), plus everything needed to build the
labeled gesture dataset on top: per-class/variation filenames, JSON
metadata sidecars, an optional repeat-cadence metronome for motion
classes, a wall-clock duration limit, and a live resync counter.

Firmware (STFT.c) computes the windowed FFT on-chip via LEA and streams
finished spectrogram columns as:

    [0xAA][0x55][0xC0][256 bytes of int8_t column data]

--- Why resync counting matters for diagnosing "weird" no_movement data ---
If a session shows structured disturbances that shouldn't be there for a
truly static scene, the first question is: is this real (an actual
moving reflector, or a sensor/power artifact) or is it corrupted/
misaligned bytes on the wire showing up as garbage columns? This script
prints a running resync count in its 2-second timing markers -- if
resyncs spike during the same windows where the data looks disturbed,
that's evidence of a communication/timing problem, not a real signal.
If resyncs stay near zero throughout, the disturbance is genuinely in
what the firmware computed and streamed, and the search should move to
the sensor/environment/power side instead.

--- Dataset path handling -------------------------------------------------
This script lives in AI_Phase/Python Files/. The dataset is stored at
AI_Phase/dataset/<gesture_class>/, i.e. OUTSIDE the Python Files folder
but still inside the AI_Phase project, so it stays alongside the
firmware/CCS project regardless of which directory you happen to run the
script from.

Pass --gesture-class to auto-generate the save path and an
auto-incremented session filename:

    python radar_dataset_capture.py --port COM7 --gesture-class no_movement

    -> AI_Phase/dataset/no_movement/no_movement_session001.txt
       (or session002, session003, ... -- picks the next free number by
       scanning what's already in that folder)

--gesture-class is restricted to the three classes currently in scope:
no_movement, horizontal_slide, closed_fist.

--- Variation tags (distance / angle / speed) ------------------------------
Optional --distance, --angle, --speed flags record the capture geometry
directly in the filename:

    python radar_dataset_capture.py --port COM7 --gesture-class horizontal_slide \
        --distance mid --angle 20 --speed medium

    -> AI_Phase/dataset/horizontal_slide/horizontal_slide_mid_20deg_session001.txt

Session numbering is scoped to the (class, distance, angle) combination.
--distance/--speed accept any short free-form tag. --angle takes a
signed integer in degrees; 0/omitted is left out of the filename.

A matching <same-stem>.json metadata sidecar is written next to each
session file: distance, angle, speed, class, timestamp, port/baud,
fixed acquisition parameters (4 kHz sampling, FFT_SIZE=256,
FFT_HOP=128, STFT_SEGMENTS=15), columns_captured, total_resyncs, and
two MEASURED values -- duration_seconds and avg_column_rate_hz (a
quick check against the ~31.25 Hz target).

--- Metronome / repeat markers (for horizontal_slide) ----------------------
--repeat-interval SECONDS cues you at a fixed cadence (terminal bell +
printed message) and logs each cue's column index into the metadata
sidecar's "repeat_markers" list, so a later segmentation script can
anchor windows to real repetitions instead of cutting randomly. Omit
for held-state classes (no_movement, closed_fist).

--- Duration limit ----------------------------------------------------------
--duration-min auto-stops the capture after N minutes of wall-clock
time, timed from when the serial connection opens.

Usage:
    python radar_dataset_capture.py --port COM7 --gesture-class no_movement --duration-min 5
    python radar_dataset_capture.py --port COM7 --gesture-class horizontal_slide \
        --distance mid --speed medium --repeat-interval 2
    python radar_dataset_capture.py --port COM7 --log-file custom_capture.txt

Dependencies:
    pip install pyserial
"""

import argparse
import json
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import serial

SYNC1, SYNC2 = 0xAA, 0x55
SPECTROGRAM_MARKER = 0xC0
RATE_MARKER = 0xC2        # 1 Hz ADC-rate snapshot, 2-byte body -- consumed & discarded here
PROFILE_MARKER = 0xC3     # STFT/DMA timing snapshot, 6-byte body -- consumed & discarded here
COLUMN_SIZE = 256         # FFT_SIZE
DC_GUARD = 5              # bins excluded on each side of center when finding the motion peak

# The three gesture classes currently in scope for dataset collection.
GESTURE_CLASSES = ["no_movement", "horizontal_slide", "closed_fist"]

# This file lives in AI_Phase/Python Files/radar_dataset_capture.py.
# .parent -> "Python Files", .parent.parent -> "AI_Phase" (project root).
# Resolving from __file__ (not cwd) means the dataset always lands in the
# right place no matter which directory you launch the script from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = PROJECT_ROOT / "dataset"


def build_variation_tag(distance: Optional[str], angle: Optional[int]) -> str:
    """("mid", 20) -> "mid_20deg", (None, 0) -> "", ("close", None) -> "close"."""
    parts = []
    if distance:
        safe_distance = "".join(c for c in distance if c.isalnum())
        if safe_distance:
            parts.append(safe_distance)
    if angle:
        sign = "" if angle >= 0 else "neg"
        parts.append(f"{sign}{abs(angle)}deg")
    return "_".join(parts)


def resolve_log_path(gesture_class: str, distance: Optional[str], angle: Optional[int]) -> Path:
    """Returns AI_Phase/dataset/<gesture_class>/<gesture_class>[_<tag>]_sessionNNN.txt,
    picking the next free NNN scoped to this exact (class, distance, angle)
    combination."""
    class_dir = DATASET_ROOT / gesture_class
    class_dir.mkdir(parents=True, exist_ok=True)

    tag = build_variation_tag(distance, angle)
    base = f"{gesture_class}_{tag}" if tag else gesture_class

    existing = sorted(class_dir.glob(f"{base}_session*.txt"))
    next_n = 1
    if existing:
        numbers = []
        for p in existing:
            stem = p.stem
            suffix = stem.rsplit("_session", 1)[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        if numbers:
            next_n = max(numbers) + 1

    return class_dir / f"{base}_session{next_n:03d}.txt"


def write_metadata_sidecar(log_path: Path, args, segment_count: int, resyncs: int,
                            capture_start: Optional[float], capture_end: Optional[float],
                            markers: Optional[list] = None):
    """Writes <session>.json next to the session .txt -- see module
    docstring for field meanings."""
    duration_seconds = None
    avg_column_rate_hz = None
    if capture_start is not None and capture_end is not None and capture_end > capture_start:
        duration_seconds = round(capture_end - capture_start, 3)
        if segment_count > 1:
            avg_column_rate_hz = round((segment_count - 1) / duration_seconds, 2)

    meta = {
        "gesture_class": args.gesture_class,
        "distance": args.distance,
        "angle_deg": args.angle,
        "speed": args.speed,
        "session_file": log_path.name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "port": args.port,
        "baud": args.baud,
        "sampling_rate_hz": 4000,   # SAMPLING_RATE_HZ in radar_configuration.h
        "fft_size": COLUMN_SIZE,    # FFT_SIZE in STFT.h
        "fft_hop": 128,             # FFT_HOP in STFT.h
        "stft_segments": 15,        # STFT_SEGMENTS in STFT.h
        "columns_captured": segment_count,
        "total_resyncs": resyncs,
        "duration_seconds": duration_seconds,
        "avg_column_rate_hz": avg_column_rate_hz,
        "repeat_interval_sec": args.repeat_interval,
        "repeat_markers": markers or [],
        "notes": "",
    }
    meta_path = log_path.with_suffix(".json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return meta_path


def find_motion_peak(column):
    """Peak away from the static DC/clutter return at center -- same logic
    as STFT_check.py."""
    center = len(column) // 2
    candidates = [(b, v) for b, v in enumerate(column) if abs(b - center) > DC_GUARD]
    return max(candidates, key=lambda x: x[1])


def parse_args():
    p = argparse.ArgumentParser(description="Text-mode dataset capture of on-chip spectrogram columns.")
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--segments", type=int, default=0, help="Stop after this many columns (0 = run until Ctrl+C)")
    p.add_argument("--duration-min", type=float, default=None,
                    help="Auto-stop after this many minutes of wall-clock capture time. "
                         "Timed from when the serial connection opens. Combines with "
                         "--segments if both are set -- whichever limit hits first stops it.")
    p.add_argument("--gesture-class", choices=GESTURE_CLASSES, default=None,
                    help="Auto-resolve the save path to AI_Phase/dataset/<class>/ with an "
                         "auto-incremented session filename. Ignored if --log-file is set.")
    p.add_argument("--distance", default=None,
                    help="Short free-form tag for capture distance, e.g. 'close', 'mid', 'far'. "
                         "Folded into the filename and metadata sidecar.")
    p.add_argument("--angle", type=int, default=0,
                    help="Radar tilt/aim angle in degrees, signed. 0 (default) is omitted "
                         "from the filename.")
    p.add_argument("--speed", default=None,
                    help="Short free-form tag for the INTENDED gesture speed, e.g. 'slow', "
                         "'medium', 'fast'.")
    p.add_argument("--repeat-interval", type=float, default=None,
                    help="Cue every N seconds (bell + message) so you can repeat a gesture on "
                         "a fixed cadence; each cue's column index is logged for later "
                         "targeted segmentation. Omit for held-state classes.")
    p.add_argument("--log-file", default=None,
                    help="Explicit output path, overrides --gesture-class auto-naming.")
    p.add_argument("--no-log", action="store_true",
                    help="Disable logging entirely (console stats only).")
    p.add_argument("--quiet", action="store_true",
                    help="Suppress the per-column stats line -- only print 2s timing markers, "
                         "metronome cues, and the final summary. Useful for long sessions "
                         "where a printed line every ~32 ms is just noise.")
    return p.parse_args()


def read_frame(ser):
    """Blocking read of one frame (mirrors STFT_check.py's read_frame_verbose,
    minus per-byte timeout/resync printing). Returns ("column", list[int]) /
    ("rate", None) / ("profile", None) / None (timeout or resync)."""
    b = ser.read(1)
    if not b or b[0] != SYNC1:
        return None
    b = ser.read(1)
    if not b or b[0] != SYNC2:
        return None
    b = ser.read(1)
    if not b:
        return None
    marker = b[0]

    if marker == SPECTROGRAM_MARKER:
        body = ser.read(COLUMN_SIZE)
        if len(body) != COLUMN_SIZE:
            return None
        column = list(struct.unpack(f"<{COLUMN_SIZE}b", body))
        return ("column", column)
    elif marker == RATE_MARKER:
        body = ser.read(2)
        if len(body) != 2:
            return None
        return ("rate", None)
    elif marker == PROFILE_MARKER:
        body = ser.read(6)
        if len(body) != 6:
            return None
        return ("profile", None)
    else:
        return None  # unrecognized marker -- treat as desync, resync on next 0xAA 0x55


def main():
    args = parse_args()

    log_path = None
    write_sidecar = False
    if not args.no_log:
        if args.log_file:
            log_path = Path(args.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
        elif args.gesture_class:
            log_path = resolve_log_path(args.gesture_class, args.distance, args.angle)
            write_sidecar = True
        else:
            print("No --gesture-class or --log-file given -- console stats only, nothing will be saved.")

    log_fh = open(log_path, "a") if log_path else None
    if log_fh:
        print(f"Logging spectrogram columns to {log_path}")

    print(f"Connecting to {args.port} @ {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"Could not open {args.port}: {e}")
        if log_fh:
            log_fh.close()
        return
    ser.reset_input_buffer()
    print("Connected. Press Ctrl+C to stop.")
    if args.duration_min:
        print(f"Will auto-stop after {args.duration_min} min.")
    if args.repeat_interval:
        print(f"Metronome: cue every {args.repeat_interval}s -- repeat the gesture at each cue.")

    segment_count = 0
    resyncs = 0
    resyncs_at_last_mark = 0
    capture_start = None
    capture_end = None
    run_start = time.time()
    metronome_start = None
    next_cue = None
    markers = []
    last_2s_mark = 0

    try:
        while True:
            now = time.time()
            elapsed = now - run_start

            # 2-second timing markers -- includes resyncs SINCE THE LAST
            # marker, not just the running total, so a spike in a specific
            # window (e.g. "resyncs jumped by 40 in the last 2s") is
            # immediately visible instead of buried in a growing total.
            current_interval = int(elapsed) // 2 * 2
            if current_interval > last_2s_mark:
                delta_resyncs = resyncs - resyncs_at_last_mark
                flag = "  <-- resync spike" if delta_resyncs > 5 else ""
                print(f"\n--- [ TIME: {current_interval:3d}.0s | columns: {segment_count} | "
                      f"resyncs this window: {delta_resyncs} | total: {resyncs} ]{flag} ---")
                resyncs_at_last_mark = resyncs
                last_2s_mark = current_interval

            if args.duration_min and elapsed / 60.0 >= args.duration_min:
                print(f"\nReached requested duration of {args.duration_min} min "
                      f"({segment_count} columns captured).")
                break

            if args.repeat_interval:
                if metronome_start is None:
                    metronome_start = now
                    next_cue = metronome_start + args.repeat_interval
                    print(f"\nMetronome started: cue every {args.repeat_interval}s.")
                while now >= next_cue:
                    marker = {
                        "cue_number": len(markers) + 1,
                        "elapsed_sec": round(now - metronome_start, 3),
                        "column_index": segment_count,
                    }
                    markers.append(marker)
                    print(f"\a*** CUE #{marker['cue_number']}  t={marker['elapsed_sec']:.2f}s "
                          f"(column {marker['column_index']}) -- repeat the gesture now ***")
                    next_cue += args.repeat_interval

            frame = read_frame(ser)
            if frame is None:
                resyncs += 1
                continue

            kind, payload = frame
            if kind != "column":
                continue

            column = payload
            segment_count += 1
            if capture_start is None:
                capture_start = time.time()
            capture_end = time.time()

            if log_fh:
                log_fh.write(" ".join(str(v) for v in column) + "\n")
                log_fh.flush()

            if not args.quiet:
                dc_peak_bin = max(range(COLUMN_SIZE), key=lambda i: column[i])
                dc_peak_val = column[dc_peak_bin]
                motion_bin, motion_val = find_motion_peak(column)
                print(f"[col {segment_count:5d} | {elapsed:6.2f}s] "
                      f"dc_peak_bin={dc_peak_bin:3d}  dc_peak_val={dc_peak_val:3d}  "
                      f"| motion_bin={motion_bin:3d}  motion_val={motion_val:3d}")

            if args.segments and segment_count >= args.segments:
                print(f"\nCaptured requested {args.segments} columns.")
                break

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()
        if log_fh:
            log_fh.close()
        print(f"\nTotal columns captured: {segment_count}")
        print(f"Total resyncs: {resyncs}")
        if log_path:
            print(f"Saved to: {log_path}")
            if write_sidecar:
                meta_path = write_metadata_sidecar(
                    log_path, args, segment_count, resyncs, capture_start, capture_end, markers)
                print(f"Metadata saved to: {meta_path}")
                if markers:
                    print(f"Logged {len(markers)} repeat marker(s).")


if __name__ == "__main__":
    main()