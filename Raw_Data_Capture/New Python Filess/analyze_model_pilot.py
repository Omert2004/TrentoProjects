"""Audit marked model-pilot captures and exported A-D windows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from filter_candidate_check import emulate_difference_q15
from raw_data import load_raw_csv
from spectrogram_view import apply_filter, compute_spectrogram


SAMPLING_RATE_HZ = 2000.0
FFT_SIZE = 256
HOP = 128
WINDOW_COLUMNS = 15
RAW_WINDOW_SPAN = FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def group_numbers(
    rows: list[dict[str, Any]], keys: tuple[str, ...], value: str
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[float]] = {}
    for row in rows:
        key = tuple(row.get(name) for name in keys)
        groups.setdefault(key, []).append(float(row[value]))
    output: list[dict[str, Any]] = []
    for key, values in sorted(groups.items(), key=lambda item: str(item[0])):
        array = np.asarray(values, dtype=float)
        result = dict(zip(keys, key))
        result.update(
            {
                "n": len(array),
                "mean": float(np.mean(array)),
                "median": float(np.median(array)),
                "p10": float(np.percentile(array, 10)),
                "p90": float(np.percentile(array, 90)),
                "minimum": float(np.min(array)),
                "maximum": float(np.max(array)),
            }
        )
        output.append(result)
    return output


def window_start(center_sample: float, total_columns: int) -> int:
    column = int(round((center_sample - RAW_WINDOW_SPAN / 2.0) / HOP))
    return max(0, min(column, total_columns - WINDOW_COLUMNS))


def band_power_db(matrix: np.ndarray, mask: np.ndarray) -> float:
    power = np.mean(np.power(10.0, matrix[mask, :] / 10.0))
    return float(10.0 * np.log10(max(power, np.finfo(float).tiny)))


def resolve_export_path(text: str, windows_root: Path) -> Path:
    path = Path(text)
    if path.is_absolute():
        return path
    root_candidate = (windows_root / path).resolve()
    if root_candidate.exists():
        return root_candidate
    working_candidate = (Path.cwd() / path).resolve()
    if working_candidate.exists():
        return working_candidate
    raise FileNotFoundError(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--windows-root", required=True)
    parser.add_argument("--session-context", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--analysis-diff-shift", type=int, default=4)
    parser.add_argument("--event-latency-s", type=float, default=0.256)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_root = Path(args.raw_root).resolve()
    windows_root = Path(args.windows_root).resolve()
    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    context_payload = json.loads(
        Path(args.session_context).read_text(encoding="utf-8-sig")
    )
    session_context = {
        str(key).lower(): value
        for key, value in context_payload.get("sessions", {}).items()
    }

    frequencies = np.fft.fftshift(
        np.fft.fftfreq(FFT_SIZE, d=1.0 / SAMPLING_RATE_HZ)
    )
    absolute_frequency = np.abs(frequencies)
    band_masks = {
        "0_20": absolute_frequency < 20,
        "20_50": (absolute_frequency >= 20) & (absolute_frequency < 50),
        "50_100": (absolute_frequency >= 50) & (absolute_frequency < 100),
        "100_250": (absolute_frequency >= 100) & (absolute_frequency < 250),
        "250_500": (absolute_frequency >= 250) & (absolute_frequency < 500),
        "500_1000": (absolute_frequency >= 500) & (absolute_frequency <= 1000),
    }

    capture_rows: list[dict[str, Any]] = []
    marker_rows: list[dict[str, Any]] = []
    event_idle_rows: list[dict[str, Any]] = []
    metadata_paths = sorted(raw_root.rglob("*.metadata.json"))

    for metadata_path in metadata_paths:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        csv_path = metadata_path.with_name(metadata["capture_csv"])
        capture = load_raw_csv(csv_path)
        session = str(metadata["session_id"]).lower()
        context = session_context.get(session, {})
        signal = capture.i.astype(float) + 1j * capture.q.astype(float)
        centered = signal - (2048.0 + 1j * 2048.0)

        capture_row: dict[str, Any] = {
            "metadata_path": str(metadata_path),
            "csv_path": str(csv_path),
            "subject_id": metadata.get("subject_id"),
            "session_id": session,
            "gesture_class": metadata.get("gesture_class"),
            "speed": metadata.get("speed"),
            "distance": metadata.get("distance") or "na",
            "posture": context.get("posture"),
            "hand_height": context.get("hand_height"),
            "sampling_rate_hz": metadata.get("configured_sampling_rate_hz"),
            "received_samples": len(capture.i),
            "metadata_received_samples": metadata.get("received_samples"),
            "observed_receive_rate_hz": metadata.get("observed_receive_rate_hz"),
            "rate_error_percent": metadata.get("receive_rate_error_percent"),
            "transport_passed": metadata.get("host_transport_validation_passed"),
            "hash_matches": sha256_file(csv_path) == metadata.get("data_sha256"),
            "event_count": len(metadata.get("event_markers", [])),
        }
        parser_stats = metadata.get("parser", {})
        for key in (
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
        ):
            capture_row[key] = int(parser_stats.get(key, 0))
        for shift in (3, 4, 5, 6):
            _filtered, clipped = emulate_difference_q15(centered, shift)
            capture_row[f"shift{shift}_clipped_samples"] = clipped
            capture_row[f"shift{shift}_clipped_percent"] = (
                100.0 * clipped / max(len(centered), 1)
            )
        capture_rows.append(capture_row)

        events = metadata.get("event_markers", [])
        for event in events:
            for boundary in ("start", "end"):
                scheduled = float(event[f"{boundary}_s"])
                actual = float(event[f"actual_{boundary}_host_elapsed_s"])
                marker_rows.append(
                    {
                        "session_id": session,
                        "gesture_class": metadata.get("gesture_class"),
                        "speed": metadata.get("speed"),
                        "distance": metadata.get("distance") or "na",
                        "posture": context.get("posture"),
                        "event_id": event.get("event_id"),
                        "boundary": boundary,
                        "scheduled_s": scheduled,
                        "actual_s": actual,
                        "timing_error_ms": 1000.0 * (actual - scheduled),
                    }
                )

        if metadata.get("gesture_class") != "clicking_hand":
            continue

        highpass = apply_filter(centered, "highpass", SAMPLING_RATE_HZ, 10.0)
        difference, _clipped = emulate_difference_q15(
            centered, args.analysis_diff_shift
        )
        spectrograms = {
            "A": compute_spectrogram(
                centered, fft_size=FFT_SIZE, hop=HOP, window_name="hann"
            ),
            "B": compute_spectrogram(
                highpass, fft_size=FFT_SIZE, hop=HOP, window_name="hann"
            ),
            "C": compute_spectrogram(
                difference, fft_size=FFT_SIZE, hop=HOP, window_name="hann"
            ),
        }
        total_columns = spectrograms["C"].shape[1]
        for event in events:
            event_center = (
                float(event["scheduled_start_sample_offset"])
                + float(event["scheduled_end_sample_offset_exclusive"])
            ) / 2.0
            event_center += args.event_latency_s * SAMPLING_RATE_HZ
            event_column = window_start(event_center, total_columns)
            if int(event["repetition"]) == 1:
                idle_center = 1.5 * SAMPLING_RATE_HZ
            else:
                idle_center = (
                    float(event["scheduled_start_sample_offset"])
                    - 1.5 * SAMPLING_RATE_HZ
                )
            idle_column = window_start(idle_center, total_columns)
            row: dict[str, Any] = {
                "session_id": session,
                "speed": metadata.get("speed"),
                "distance": metadata.get("distance") or "na",
                "posture": context.get("posture"),
                "hand_height": context.get("hand_height"),
                "event_id": event.get("event_id"),
                "repetition": event.get("repetition"),
                "event_latency_s": args.event_latency_s,
            }
            for pipeline, matrix in spectrograms.items():
                event_window = matrix[
                    :, event_column : event_column + WINDOW_COLUMNS
                ]
                idle_window = matrix[:, idle_column : idle_column + WINDOW_COLUMNS]
                for band, mask in band_masks.items():
                    event_db = band_power_db(event_window, mask)
                    idle_db = band_power_db(idle_window, mask)
                    row[f"pipeline_{pipeline}_{band}_event_minus_idle_db"] = (
                        event_db - idle_db
                    )
            event_idle_rows.append(row)

    manifest_path = windows_root / "paired_windows_manifest.jsonl"
    window_entries = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    window_rows: list[dict[str, Any]] = []
    for entry in window_entries:
        row: dict[str, Any] = {
            key: entry.get(key)
            for key in (
                "window_id",
                "subject_id",
                "session_id",
                "split",
                "gesture_class",
                "speed",
                "distance",
                "posture",
                "hand_height",
                "event_id",
                "repetition",
            )
        }
        for pipeline in ("a", "b", "c", "d"):
            matrix = np.load(
                resolve_export_path(entry[f"pipeline_{pipeline}_path"], windows_root),
                allow_pickle=False,
            )
            if matrix.shape != (FFT_SIZE, WINDOW_COLUMNS):
                raise ValueError(f"bad tensor shape for {entry['window_id']}")
            values = matrix[absolute_frequency > 20, :]
            median = float(np.median(values))
            row[f"pipeline_{pipeline.upper()}_outside_dc_median_db"] = median
            row[f"pipeline_{pipeline.upper()}_outside_dc_p99_contrast_db"] = (
                float(np.percentile(values, 99)) - median
            )
            for band, mask in band_masks.items():
                row[f"pipeline_{pipeline.upper()}_{band}_power_db"] = band_power_db(
                    matrix, mask
                )
        row["pipeline_D_kept_pixels"] = int(entry["pipeline_d"]["kept_pixels"])
        row["pipeline_D_kept_components"] = int(
            entry["pipeline_d"]["kept_components"]
        )
        window_rows.append(row)

    write_csv(out_root / "capture_audit.csv", capture_rows)
    write_csv(out_root / "marker_timing.csv", marker_rows)
    write_csv(out_root / "event_vs_idle.csv", event_idle_rows)
    write_csv(out_root / "window_metrics.csv", window_rows)

    transport_keys = (
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
    rate_values = np.asarray(
        [float(row["observed_receive_rate_hz"]) for row in capture_rows]
    )
    timing_values = np.asarray(
        [float(row["timing_error_ms"]) for row in marker_rows]
    )
    clipping: dict[str, Any] = {}
    for shift in (3, 4, 5, 6):
        counts = np.asarray(
            [int(row[f"shift{shift}_clipped_samples"]) for row in capture_rows]
        )
        percents = np.asarray(
            [float(row[f"shift{shift}_clipped_percent"]) for row in capture_rows]
        )
        worst_index = int(np.argmax(percents))
        clipping[f"shift{shift}"] = {
            "total_clipped_samples": int(np.sum(counts)),
            "captures_over_0_1_percent": int(np.count_nonzero(percents > 0.1)),
            "worst_capture_percent": float(percents[worst_index]),
            "worst_capture": capture_rows[worst_index]["csv_path"],
        }

    summary = {
        "schema_version": 1,
        "captures": len(capture_rows),
        "metadata_hashes_match": all(row["hash_matches"] for row in capture_rows),
        "transport_passed": all(row["transport_passed"] for row in capture_rows),
        "transport_error_totals": {
            key: int(sum(int(row[key]) for row in capture_rows))
            for key in transport_keys
        },
        "observed_receive_rate_hz": {
            "minimum": float(np.min(rate_values)),
            "mean": float(np.mean(rate_values)),
            "maximum": float(np.max(rate_values)),
        },
        "markers": {
            "count": len(marker_rows),
            "timing_error_ms_minimum": float(np.min(timing_values)),
            "timing_error_ms_mean": float(np.mean(timing_values)),
            "timing_error_ms_maximum": float(np.max(timing_values)),
            "timing_error_ms_p95": float(np.percentile(timing_values, 95)),
        },
        "difference_clipping": clipping,
        "windows": {
            "paired_abcd": len(window_rows),
            "by_split_and_class": group_numbers(
                [dict(row, count=1) for row in window_rows],
                ("split", "gesture_class"),
                "count",
            ),
            "pipeline_d_zero_windows_by_class": {
                gesture_class: {
                    "windows": sum(
                        row["gesture_class"] == gesture_class for row in window_rows
                    ),
                    "zero_kept_pixels": sum(
                        row["gesture_class"] == gesture_class
                        and row["pipeline_D_kept_pixels"] == 0
                        for row in window_rows
                    ),
                }
                for gesture_class in ("empty", "clicking_hand")
            },
        },
        "event_vs_idle_db": {
            "by_pipeline_band": {
                f"{pipeline}_{band}": group_numbers(
                    event_idle_rows,
                    ("posture", "distance", "speed"),
                    f"pipeline_{pipeline}_{band}_event_minus_idle_db",
                )
                for pipeline in ("A", "B", "C")
                for band in band_masks
            }
        },
    }
    (out_root / "analysis_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Audited {len(capture_rows)} captures and {len(window_rows)} A-D windows.")
    print(f"Results: {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
