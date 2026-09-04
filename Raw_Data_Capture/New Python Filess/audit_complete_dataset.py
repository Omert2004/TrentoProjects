from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (
    SCRIPT_DIR,
    SCRIPT_DIR / "Raw_Data_Capture",
    SCRIPT_DIR.parent / "Raw_Data_Capture",
    SCRIPT_DIR.parent.parent / "Raw_Data_Capture",
    Path.cwd(),
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from filter_candidate_check import emulate_difference_q15  # noqa: E402
from raw_data import load_raw_csv  # noqa: E402
from spectrogram_view import compute_spectrogram  # noqa: E402


FS = 2000.0
FFT_SIZE = 256
HOP = 128
WINDOW_COLUMNS = 15
RAW_SPAN = FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP
SESSIONS = tuple(f"session{i:02d}" for i in range(1, 6))
SPEEDS = ("slow", "normal", "fast")
DISTANCES = ("near", "mid", "far")
GESTURES = (
    "clicking_hand",
    "left_horizontal_scroll",
    "right_horizontal_scroll",
)
CLASSES = ("empty",) + GESTURES
TRANSPORT_KEYS = (
    "crc_errors",
    "invalid_headers",
    "invalid_adc_packets",
    "resync_events",
    "packet_sequence_gaps",
    "missing_packets",
    "packet_sequence_reorders",
    "sample_index_gaps",
    "missing_samples",
    "sample_index_reorders",
    "reported_drop_increase",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def expected_keys() -> set[tuple[str, str, str, str]]:
    keys: set[tuple[str, str, str, str]] = set()
    for session in SESSIONS:
        keys.update((session, "empty", speed, "na") for speed in SPEEDS)
        keys.update(
            (session, gesture, speed, distance)
            for gesture in GESTURES
            for speed in SPEEDS
            for distance in DISTANCES
        )
    return keys


def window_start(center_sample: float, columns: int) -> int:
    value = int(round((center_sample - RAW_SPAN / 2.0) / HOP))
    return max(0, min(value, columns - WINDOW_COLUMNS))


def power(matrix: np.ndarray, mask: np.ndarray, columns: slice | None = None) -> float:
    values = matrix[mask, :] if columns is None else matrix[mask, columns]
    return float(np.mean(np.power(10.0, values / 10.0)))


def ratio_db(numerator: float, denominator: float) -> float:
    tiny = np.finfo(float).tiny
    return float(10.0 * np.log10(max(numerator, tiny) / max(denominator, tiny)))


def describe(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            "minimum": None,
            "p10": None,
            "median": None,
            "mean": None,
            "p90": None,
            "maximum": None,
        }
    array = np.asarray(values, dtype=float)
    return {
        "n": len(array),
        "minimum": float(np.min(array)),
        "p10": float(np.percentile(array, 10)),
        "median": float(np.median(array)),
        "mean": float(np.mean(array)),
        "p90": float(np.percentile(array, 90)),
        "maximum": float(np.max(array)),
    }


def auc(right: list[float], left: list[float]) -> float:
    comparisons = [
        float(r > l) + 0.5 * float(r == l)
        for r in right
        for l in left
    ]
    return float(np.mean(comparisons))


def group_event_gain(
    rows: list[dict[str, object]], keys: tuple[str, ...]
) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[float]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(
            float(row["event_minus_idle_20_250_db"])
        )
    output: list[dict[str, object]] = []
    for group, values in sorted(groups.items(), key=lambda item: str(item[0])):
        item = dict(zip(keys, group))
        item.update(describe(values))
        item["positive_percent"] = float(
            100.0 * np.count_nonzero(np.asarray(values) > 0) / len(values)
        )
        output.append(item)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the complete four-class 2 kHz model-pilot raw dataset. "
            "Run this file beside raw_data.py, filter_candidate_check.py, "
            "and spectrogram_view.py."
        )
    )
    parser.add_argument(
        "--model-pilot-root",
        default="dataset/model-pilot",
        help="Path containing raw/, capture_manifest.jsonl, and session_context.json.",
    )
    parser.add_argument("--subject", default="subject01")
    parser.add_argument(
        "--session-context",
        default=None,
        help="Optional context JSON; defaults to <model-pilot-root>/session_context.json.",
    )
    parser.add_argument(
        "--out",
        default="dataset/model-pilot/audit-results",
        help="Output directory for audit_summary.json and CSV evidence.",
    )
    parser.add_argument(
        "--analysis-diff-shift",
        type=int,
        default=4,
        help="Difference-filter shift used only for the signal-quality STFT screen.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_root = Path(args.model_pilot_root).resolve()
    raw_root = model_root / "raw" / "fs2000" / args.subject
    output_root = Path(args.out).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if not model_root.is_dir():
        raise FileNotFoundError(f"model-pilot root not found: {model_root}")
    if not raw_root.is_dir():
        raise FileNotFoundError(f"subject raw-data root not found: {raw_root}")

    context_path = (
        Path(args.session_context).resolve()
        if args.session_context
        else model_root / "session_context.json"
    )
    context = (
        json.loads(context_path.read_text(encoding="utf-8-sig")).get("sessions", {})
        if context_path.exists()
        else {}
    )

    frequencies = np.fft.fftshift(np.fft.fftfreq(FFT_SIZE, 1.0 / FS))
    absolute = np.abs(frequencies)
    bands = {
        "0_20": absolute < 20,
        "20_50": (absolute >= 20) & (absolute < 50),
        "50_250": (absolute >= 50) & (absolute < 250),
        "20_250": (absolute >= 20) & (absolute < 250),
    }
    band_20_250 = bands["20_250"]
    positive = (frequencies >= 20) & (frequencies < 250)
    negative = (frequencies <= -20) & (frequencies > -250)

    capture_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    main_keys: list[tuple[str, str, str, str]] = []
    parser_totals = Counter()
    marker_errors: list[float] = []
    sample_errors: list[str] = []
    schedule_errors: list[str] = []
    name_errors: list[str] = []
    rail_total = 0

    metadata_paths = sorted(raw_root.rglob("*.metadata.json"))
    direction_paths = sorted(
        (model_root / "direction-validation").rglob("*.metadata.json")
    )

    for metadata_path in metadata_paths + direction_paths:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        csv_path = metadata_path.with_name(metadata["capture_csv"])
        capture = load_raw_csv(csv_path)
        rows = len(capture.i)
        parser = metadata.get("parser", {})
        for key in TRANSPORT_KEYS:
            parser_totals[key] += int(parser.get(key, 0))

        if rows != int(metadata.get("received_samples", -1)):
            sample_errors.append(f"row/metadata mismatch: {csv_path}")
        if rows != int(parser.get("samples_accepted", -1)):
            sample_errors.append(f"row/parser mismatch: {csv_path}")

        gesture = str(metadata.get("gesture_class") or "alternating_scroll")
        session = str(metadata.get("session_id"))
        speed = str(metadata.get("speed"))
        distance = str(metadata.get("distance") or "na")
        purpose = str(metadata.get("capture_purpose"))
        is_main = session in SESSIONS
        is_scrollpilot = session == "scrollpilot01"
        is_direction = session == "directioncheck01"
        events = metadata.get("event_markers", []) or []

        if is_main:
            key = (session, gesture, speed, distance)
            main_keys.append(key)
            prefix = "_".join(key)
            expected_prefix = f"{args.subject}_{prefix}_"
            if not metadata_path.name.startswith(expected_prefix):
                name_errors.append(str(metadata_path))

        signal = (
            capture.i.astype(float)
            - 2048.0
            + 1j * (capture.q.astype(float) - 2048.0)
        )
        rail_count = int(
            np.count_nonzero(
                (capture.i == 0)
                | (capture.i == 4095)
                | (capture.q == 0)
                | (capture.q == 4095)
            )
        )
        rail_total += rail_count

        row: dict[str, object] = {
            "scope": (
                "main" if is_main else "scrollpilot" if is_scrollpilot else "direction"
            ),
            "metadata_path": str(metadata_path),
            "csv_path": str(csv_path),
            "session_id": session,
            "gesture_class": gesture,
            "speed": speed,
            "distance": distance,
            "posture": context.get(session, {}).get("posture"),
            "hand_height": context.get(session, {}).get("hand_height"),
            "started_utc": metadata.get("started_utc"),
            "received_samples": rows,
            "metadata_received_samples": metadata.get("received_samples"),
            "observed_receive_rate_hz": metadata.get("observed_receive_rate_hz"),
            "rate_error_percent": metadata.get("receive_rate_error_percent"),
            "transport_passed": metadata.get("host_transport_validation_passed"),
            "scientific_continuity": metadata.get("scientific_sample_continuity_proven"),
            "hash_matches": sha256_file(csv_path) == metadata.get("data_sha256"),
            "event_count": len(events),
            "adc_rail_samples": rail_count,
            "segment_count": len(capture.segment_ids()),
        }
        for key in TRANSPORT_KEYS:
            row[key] = int(parser.get(key, 0))
        for shift in (3, 4, 5, 6):
            _filtered, clipped = emulate_difference_q15(signal, shift)
            row[f"shift{shift}_clipped_samples"] = clipped
            row[f"shift{shift}_clipped_percent"] = 100.0 * clipped / rows
        capture_rows.append(row)

        if is_main and gesture == "empty":
            if events:
                schedule_errors.append(f"empty has events: {metadata_path}")
            continue
        if not events:
            continue

        if is_main or is_scrollpilot:
            expected_starts = [3.0, 8.0, 13.0, 18.0, 23.0]
            expected_ends = [4.0, 9.0, 14.0, 19.0, 24.0]
            if len(events) != 5:
                schedule_errors.append(f"wrong event count: {metadata_path}")
            else:
                actual_starts = [float(event["start_s"]) for event in events]
                actual_ends = [float(event["end_s"]) for event in events]
                if actual_starts != expected_starts or actual_ends != expected_ends:
                    schedule_errors.append(f"wrong schedule: {metadata_path}")
                expected_direction = {
                    "clicking_hand": "open-to-fist",
                    "left_horizontal_scroll": "right-to-left",
                    "right_horizontal_scroll": "left-to-right",
                }[gesture]
                expected_motion = {"slow": 0.75, "normal": 0.5, "fast": 0.25}[speed]
                for event in events:
                    if event.get("direction") != expected_direction:
                        schedule_errors.append(f"wrong direction: {metadata_path}")
                    if float(event.get("motion_duration_s")) != expected_motion:
                        schedule_errors.append(f"wrong motion duration: {metadata_path}")

        for event in events:
            for boundary in ("start", "end"):
                marker_errors.append(
                    1000.0
                    * (
                        float(event[f"actual_{boundary}_host_elapsed_s"])
                        - float(event[f"{boundary}_s"])
                    )
                )

        if not is_main or gesture == "empty":
            continue

        difference, _clipped = emulate_difference_q15(
            signal, args.analysis_diff_shift
        )
        spectrogram = compute_spectrogram(
            difference, fft_size=FFT_SIZE, hop=HOP, window_name="hann"
        )
        total_columns = spectrogram.shape[1]
        for event in events:
            center = (
                float(event["scheduled_start_sample_offset"])
                + float(event["scheduled_end_sample_offset_exclusive"])
            ) / 2.0
            idle_center = (
                1.5 * FS
                if int(event["repetition"]) == 1
                else float(event["scheduled_start_sample_offset"]) - 1.5 * FS
            )
            idle_column = window_start(idle_center, total_columns)
            idle_matrix = spectrogram[:, idle_column : idle_column + WINDOW_COLUMNS]
            latency_gains: dict[str, float] = {}
            for latency_s in (0.0, 0.128, 0.256, 0.384, 0.512):
                latency_column = window_start(
                    center + latency_s * FS, total_columns
                )
                latency_matrix = spectrogram[
                    :, latency_column : latency_column + WINDOW_COLUMNS
                ]
                for band_name, band_mask in bands.items():
                    latency_gains[
                        f"event_minus_idle_{band_name}_latency_{int(latency_s * 1000):03d}ms_db"
                    ] = ratio_db(
                        power(latency_matrix, band_mask),
                        power(idle_matrix, band_mask),
                    )
            event_column = window_start(center, total_columns)
            event_matrix = spectrogram[
                :, event_column : event_column + WINDOW_COLUMNS
            ]
            positive_gain = ratio_db(
                power(event_matrix, positive), power(idle_matrix, positive)
            )
            negative_gain = ratio_db(
                power(event_matrix, negative), power(idle_matrix, negative)
            )
            first = slice(0, 7)
            second = slice(8, 15)
            first_balance = ratio_db(
                power(event_matrix, positive, first),
                power(event_matrix, negative, first),
            )
            second_balance = ratio_db(
                power(event_matrix, positive, second),
                power(event_matrix, negative, second),
            )
            event_row: dict[str, object] = {
                    "session_id": session,
                    "split": (
                        "train"
                        if session in ("session01", "session02", "session03")
                        else "validation" if session == "session04" else "test"
                    ),
                    "gesture_class": gesture,
                    "speed": speed,
                    "distance": distance,
                    "posture": context.get(session, {}).get("posture"),
                    "hand_height": context.get(session, {}).get("hand_height"),
                    "event_id": event.get("event_id"),
                    "repetition": event.get("repetition"),
                    "event_minus_idle_20_250_db": ratio_db(
                        power(event_matrix, band_20_250),
                        power(idle_matrix, band_20_250),
                    ),
                    "positive_gain_db": positive_gain,
                    "negative_gain_db": negative_gain,
                    "signed_side_gain_score_db": positive_gain - negative_gain,
                    "signed_sequence_score_db": first_balance - second_balance,
                }
            event_row.update(latency_gains)
            event_rows.append(event_row)

    expected = expected_keys()
    counts = Counter(main_keys)
    missing = sorted(expected - set(counts))
    unexpected = sorted(set(counts) - expected)
    duplicates = sorted(key for key, count in counts.items() if count != 1)

    manifest_path = model_root / "capture_manifest.jsonl"
    if not manifest_path.exists():
        raise FileNotFoundError(f"capture manifest not found: {manifest_path}")
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    manifest_keys = [
        (
            row["session_id"],
            row["gesture_class"],
            row["speed"],
            row.get("distance") or "na",
        )
        for row in manifest_rows
        if row["session_id"] in SESSIONS
    ]

    rates = [
        float(row["observed_receive_rate_hz"])
        for row in capture_rows
        if row["scope"] == "main"
    ]
    rate_errors = [
        float(row["rate_error_percent"])
        for row in capture_rows
        if row["scope"] == "main"
    ]
    clipping: dict[str, object] = {}
    main_captures = [row for row in capture_rows if row["scope"] == "main"]
    for shift in (3, 4, 5, 6):
        percentages = [
            float(row[f"shift{shift}_clipped_percent"]) for row in main_captures
        ]
        worst = int(np.argmax(percentages))
        clipping[f"shift{shift}"] = {
            "total_clipped_samples": int(
                sum(int(row[f"shift{shift}_clipped_samples"]) for row in main_captures)
            ),
            "captures_with_any_clipping": int(sum(value > 0 for value in percentages)),
            "captures_over_0_1_percent": int(sum(value > 0.1 for value in percentages)),
            "worst_capture_percent": percentages[worst],
            "worst_capture": main_captures[worst]["csv_path"],
        }

    direction: dict[str, object] = {}
    for score in ("signed_side_gain_score_db", "signed_sequence_score_db"):
        direction[score] = {}
        for distance in ("all",) + DISTANCES:
            selected = [
                row
                for row in event_rows
                if row["gesture_class"]
                in ("left_horizontal_scroll", "right_horizontal_scroll")
                and (distance == "all" or row["distance"] == distance)
            ]
            left = [
                float(row[score])
                for row in selected
                if row["gesture_class"] == "left_horizontal_scroll"
            ]
            right = [
                float(row[score])
                for row in selected
                if row["gesture_class"] == "right_horizontal_scroll"
            ]
            value = auc(right, left)
            direction[score][distance] = {
                "auc_right_greater": value,
                "unsigned_separability_auc": max(value, 1.0 - value),
                "left_median": float(np.median(left)),
                "right_median": float(np.median(right)),
            }

    capture_dates: dict[str, list[str]] = defaultdict(list)
    for row in main_captures:
        capture_dates[str(row["gesture_class"])].append(str(row["started_utc"]))
    date_summary: dict[str, object] = {}
    for gesture, values in capture_dates.items():
        parsed = [datetime.fromisoformat(value) for value in values]
        date_summary[gesture] = {
            "first_utc": min(parsed).isoformat(),
            "last_utc": max(parsed).isoformat(),
            "calendar_dates": sorted({value.date().isoformat() for value in parsed}),
        }

    summary = {
        "archive_inventory": {
            "main_captures": sum(row["scope"] == "main" for row in capture_rows),
            "scrollpilot_captures": sum(
                row["scope"] == "scrollpilot" for row in capture_rows
            ),
            "direction_captures": sum(
                row["scope"] == "direction" for row in capture_rows
            ),
            "capture_manifest_rows": len(manifest_rows),
        },
        "matrix": {
            "expected_cells": len(expected),
            "observed_cells": len(main_keys),
            "missing": missing,
            "unexpected": unexpected,
            "duplicates": duplicates,
            "manifest_main_rows": len(manifest_keys),
            "manifest_main_unique": len(set(manifest_keys)),
        },
        "integrity": {
            "all_hashes_match": all(row["hash_matches"] for row in capture_rows),
            "all_transport_pass": all(
                row["transport_passed"] for row in capture_rows
            ),
            "all_scientific_continuity": all(
                row["scientific_continuity"] for row in capture_rows
            ),
            "parser_error_totals": dict(parser_totals),
            "sample_errors": sample_errors,
            "schedule_errors": schedule_errors,
            "name_errors": name_errors,
            "adc_rail_samples": rail_total,
            "all_single_segment": all(
                int(row["segment_count"]) == 1 for row in capture_rows
            ),
        },
        "rate": {
            "observed_receive_rate_hz": describe(rates),
            "receive_rate_error_percent": describe(rate_errors),
        },
        "marker_timing_error_ms_all_events": describe(marker_errors),
        "clipping_main_150": clipping,
        "event_gain_by_class_distance": group_event_gain(
            event_rows, ("gesture_class", "distance")
        ),
        "event_gain_by_class_speed": group_event_gain(
            event_rows, ("gesture_class", "speed")
        ),
        "event_gain_by_class_posture": group_event_gain(
            event_rows, ("gesture_class", "posture")
        ),
        "direction_screen": direction,
        "capture_date_summary": date_summary,
        "configuration": {
            "model_pilot_root": str(model_root),
            "subject": args.subject,
            "session_context": str(context_path) if context_path.exists() else None,
            "analysis_diff_shift": args.analysis_diff_shift,
            "fft_size": FFT_SIZE,
            "hop": HOP,
            "window_columns": WINDOW_COLUMNS,
        },
    }

    write_csv(output_root / "capture_audit.csv", capture_rows)
    write_csv(output_root / "event_metrics.csv", event_rows)
    write_csv(
        output_root / "event_gain_by_class_distance.csv",
        summary["event_gain_by_class_distance"],
    )
    write_csv(
        output_root / "event_gain_by_class_speed.csv",
        summary["event_gain_by_class_speed"],
    )
    write_csv(
        output_root / "event_gain_by_class_posture.csv",
        summary["event_gain_by_class_posture"],
    )
    (output_root / "audit_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    print(f"\nAudit outputs written to: {output_root}")


if __name__ == "__main__":
    main()
