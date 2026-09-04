"""Export paired A-D numeric 256x15 tensors from marked 2 kHz pilot captures.

Pipeline A is centered raw I/Q + STFT. Pipeline B adds a first-order high-pass.
Pipeline C is difference + configurable Q15 shift + STFT. Pipeline D starts
from the identical C tensor and applies a causal per-window threshold and
8-connected component filter. Source sessions are assigned to a split before
any windows are created, preventing overlap leakage. A selected subset of
complete classes may be exported for a preliminary audit before the full
four-class matrix is available.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

from filter_candidate_check import connected_component_filter, emulate_difference_q15
from raw_data import load_raw_csv
from spectrogram_view import apply_filter, compute_spectrogram, positive_int
from timed_pilot_capture import PILOT_ACTIONS, PILOT_CLASSES, PILOT_SPEEDS


FFT_SIZE = 256
HOP = 128
WINDOW_COLUMNS = 15
SAMPLING_RATE_HZ = 2000.0
DEFAULT_DIFF_SHIFT = 4


def positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def split_map(args: argparse.Namespace) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for split, sessions in (
        ("train", args.train_sessions),
        ("validation", args.validation_sessions),
        ("test", args.test_sessions),
    ):
        for session in sessions:
            key = session.lower()
            if key in mapping:
                raise ValueError(f"session {session!r} appears in multiple splits")
            mapping[key] = split
    return mapping


def expected_matrix_cells(classes: tuple[str, ...]) -> set[tuple[str, str, str]]:
    cells: set[tuple[str, str, str]] = set()
    for gesture_class in classes:
        if gesture_class == "empty":
            cells.update((gesture_class, speed, "na") for speed in PILOT_SPEEDS)
        else:
            cells.update(
                (gesture_class, speed, distance)
                for speed in PILOT_SPEEDS
                for distance in ("near", "mid", "far")
            )
    return cells


def load_session_context(path: str | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    context_path = Path(path)
    payload = json.loads(context_path.read_text(encoding="utf-8-sig"))
    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        raise ValueError(f"session context lacks a sessions object: {context_path}")
    output: dict[str, dict[str, Any]] = {}
    for session, values in sessions.items():
        if not isinstance(values, dict):
            raise ValueError(f"invalid context for session {session!r}")
        output[str(session).lower()] = dict(values)
    return output


def clamp_window_start(start_column: int, total_columns: int) -> int:
    return max(0, min(start_column, total_columns - WINDOW_COLUMNS))


def event_window_specs(
    event: dict[str, Any],
    *,
    total_columns: int,
    marker_guard_s: float,
) -> list[dict[str, Any]]:
    raw_span = FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP
    start_sample = int(event["scheduled_start_sample_offset"])
    end_sample = int(event["scheduled_end_sample_offset_exclusive"])
    guard_samples = int(round(marker_guard_s * SAMPLING_RATE_HZ))
    guarded_start = start_sample + guard_samples
    guarded_end = end_sample - guard_samples
    if guarded_end <= guarded_start:
        guarded_start, guarded_end = start_sample, end_sample

    duration = guarded_end - guarded_start
    starts: list[tuple[int, str]] = []
    if duration <= raw_span:
        center = (start_sample + end_sample) / 2.0
        start_column = int(round((center - raw_span / 2.0) / HOP))
        starts.append((clamp_window_start(start_column, total_columns), "contains_complete_event"))
    else:
        first = int(np.ceil(guarded_start / HOP))
        last = int(np.floor((guarded_end - raw_span) / HOP))
        first = clamp_window_start(first, total_columns)
        last = clamp_window_start(last, total_columns)
        starts.append((first, "inside_event"))
        if last != first:
            starts.append((last, "inside_event"))

    output = []
    seen: set[int] = set()
    for start_column, relation in starts:
        if start_column in seen:
            continue
        seen.add(start_column)
        output.append(
            {
                "start_column": start_column,
                "event_id": event.get("event_id"),
                "repetition": event.get("repetition"),
                "direction": event.get("direction"),
                "window_relation": relation,
            }
        )
    return output


def static_window_specs(
    *,
    total_columns: int,
    edge_guard_s: float,
) -> list[dict[str, Any]]:
    first_column = int(np.ceil(edge_guard_s * SAMPLING_RATE_HZ / HOP))
    last_exclusive = total_columns - first_column
    return [
        {
            "start_column": start,
            "event_id": None,
            "repetition": None,
            "direction": None,
            "window_relation": "static_interval",
        }
        for start in range(first_column, last_exclusive - WINDOW_COLUMNS + 1, WINDOW_COLUMNS)
    ]


def clustered_tensor(
    tensor: np.ndarray,
    *,
    frequencies: np.ndarray,
    dc_guard_hz: float,
    threshold_db: float,
    minimum_pixels: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    outside_dc = np.abs(frequencies) > dc_guard_hz
    noise_floor = float(np.median(tensor[outside_dc, :]))
    mask = tensor >= noise_floor + threshold_db
    mask[~outside_dc, :] = False
    kept, kept_components, removed_components = connected_component_filter(
        mask, minimum_pixels
    )
    output = np.where(kept, tensor, noise_floor).astype(np.float32)
    return output, {
        "noise_floor_db": noise_floor,
        "kept_pixels": int(np.count_nonzero(kept)),
        "kept_components": kept_components,
        "removed_components": removed_components,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root",
        default="dataset/model-pilot/raw/fs2000",
    )
    parser.add_argument(
        "--out",
        default="dataset/model-pilot/windows",
    )
    parser.add_argument("--train-sessions", nargs="+", required=True)
    parser.add_argument("--validation-sessions", nargs="+", required=True)
    parser.add_argument("--test-sessions", nargs="+", required=True)
    parser.add_argument(
        "--classes",
        nargs="+",
        choices=PILOT_CLASSES,
        default=list(PILOT_CLASSES),
        help="Complete classes to export (default: all four).",
    )
    parser.add_argument(
        "--session-context",
        help="Optional JSON file mapping session IDs to posture/height metadata.",
    )
    parser.add_argument("--static-edge-guard", type=positive_float, default=1.0)
    parser.add_argument(
        "--diff-shift",
        type=positive_int,
        default=DEFAULT_DIFF_SHIFT,
        help=f"Q15 difference scaling shift (default: {DEFAULT_DIFF_SHIFT}).",
    )
    parser.add_argument("--highpass-hz", type=positive_float, default=10.0)
    parser.add_argument("--dc-guard-hz", type=positive_float, default=20.0)
    parser.add_argument("--cluster-threshold-db", type=positive_float, default=12.0)
    parser.add_argument("--cluster-min-pixels", type=positive_int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        sessions = split_map(args)
        selected_classes = tuple(dict.fromkeys(args.classes))
        session_context = load_session_context(args.session_context)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    input_root = Path(args.input_root)
    metadata_paths = sorted(input_root.rglob("*.metadata.json"))
    if not metadata_paths:
        print(f"Error: no pilot metadata found under {input_root}.", file=sys.stderr)
        return 2

    out_root = Path(args.out)
    manifest_path = out_root / "paired_windows_manifest.jsonl"
    if manifest_path.exists():
        print(f"Error: refusing to overwrite {manifest_path}.", file=sys.stderr)
        return 2

    frequencies = np.fft.fftshift(
        np.fft.fftfreq(FFT_SIZE, d=1.0 / SAMPLING_RATE_HZ)
    )
    manifest_entries: list[dict[str, Any]] = []
    counts: Counter[tuple[str, str, str, str]] = Counter()
    validated_records: dict[
        tuple[str, str, str, str, str], tuple[Path, dict[str, Any]]
    ] = {}
    skipped_failed = 0
    skipped_unselected = 0

    for metadata_path in metadata_paths:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not metadata.get("host_transport_validation_passed", False):
            skipped_failed += 1
            continue
        if float(metadata.get("configured_sampling_rate_hz", 0)) != SAMPLING_RATE_HZ:
            print(f"Error: non-2 kHz source capture {metadata_path}.", file=sys.stderr)
            return 2
        session = str(metadata.get("session_id", "")).lower()
        if session not in sessions:
            print(f"Error: session {session!r} has no split assignment.", file=sys.stderr)
            return 2
        gesture_class = str(metadata.get("gesture_class") or metadata.get("condition", ""))
        speed = str(metadata.get("speed", ""))
        if gesture_class not in PILOT_CLASSES:
            print(
                f"Error: unknown gesture class {gesture_class!r} in {metadata_path}.",
                file=sys.stderr,
            )
            return 2
        if gesture_class not in selected_classes:
            skipped_unselected += 1
            continue
        if speed not in PILOT_SPEEDS:
            print(f"Error: missing/unknown speed {speed!r} in {metadata_path}.", file=sys.stderr)
            return 2
        record_key = (
            str(metadata.get("subject_id", "")).lower(),
            session,
            gesture_class,
            speed,
            str(metadata.get("distance") or "na"),
        )
        if record_key in validated_records:
            print(
                f"Error: multiple validated takes exist for {record_key}; "
                "move the unwanted duplicate out of the pilot root.",
                file=sys.stderr,
            )
            return 2
        validated_records[record_key] = (metadata_path, metadata)

    if not validated_records:
        print("Error: no validated pilot captures were found.", file=sys.stderr)
        return 2

    expected_cells = expected_matrix_cells(selected_classes)
    matrix_cells: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    for subject, session, gesture_class, speed, distance in validated_records:
        matrix_cells.setdefault((subject, session), set()).add(
            (gesture_class, speed, distance)
        )
    for subject_session, cells in matrix_cells.items():
        missing = sorted(expected_cells - cells)
        extra = sorted(cells - expected_cells)
        if missing or extra:
            print(
                f"Error: incomplete pilot matrix {subject_session}; "
                f"missing={missing}, extra={extra}.",
                file=sys.stderr,
            )
            return 2
    represented_sessions = {session for _subject, session in matrix_cells}
    absent_sessions = sorted(set(sessions) - represented_sessions)
    if absent_sessions:
        print(
            f"Error: assigned sessions have no complete matrix: {absent_sessions}.",
            file=sys.stderr,
        )
        return 2

    for metadata_path, metadata in validated_records.values():
        session = str(metadata["session_id"]).lower()
        split = sessions[session]
        csv_path = metadata_path.with_name(metadata["capture_csv"])
        capture = load_raw_csv(csv_path)
        centered_signal = (
            capture.i.astype(float) - 2048.0
            + 1j * (capture.q.astype(float) - 2048.0)
        )
        highpass_signal = apply_filter(
            centered_signal, "highpass", SAMPLING_RATE_HZ, args.highpass_hz
        )
        difference_signal, clipped_samples = emulate_difference_q15(
            centered_signal, args.diff_shift
        )
        spectrogram_a = compute_spectrogram(
            centered_signal,
            fft_size=FFT_SIZE,
            hop=HOP,
            window_name="hann",
        )
        spectrogram_b = compute_spectrogram(
            highpass_signal,
            fft_size=FFT_SIZE,
            hop=HOP,
            window_name="hann",
        )
        spectrogram_c = compute_spectrogram(
            difference_signal,
            fft_size=FFT_SIZE,
            hop=HOP,
            window_name="hann",
        )

        gesture_class = str(metadata["gesture_class"])
        speed = str(metadata["speed"])
        distance = str(metadata.get("distance") or "na")
        subject = str(metadata["subject_id"])
        marker_guard = float(metadata.get("marker_guard_s", 0.15))
        context = session_context.get(session, {})
        if gesture_class in PILOT_ACTIONS:
            events = metadata.get("event_markers")
            if not events:
                print(
                    f"Error: action capture lacks event markers: {metadata_path}",
                    file=sys.stderr,
                )
                return 2
            specs = []
            for event in events:
                specs.extend(
                    event_window_specs(
                        event,
                        total_columns=spectrogram_c.shape[1],
                        marker_guard_s=marker_guard,
                    )
                )
        else:
            specs = static_window_specs(
                total_columns=spectrogram_c.shape[1],
                edge_guard_s=args.static_edge_guard,
            )

        for local_index, spec in enumerate(specs, start=1):
            start_column = int(spec["start_column"])
            stop_column = start_column + WINDOW_COLUMNS
            tensor_a = spectrogram_a[:, start_column:stop_column].astype(np.float32)
            tensor_b = spectrogram_b[:, start_column:stop_column].astype(np.float32)
            tensor_c = spectrogram_c[:, start_column:stop_column].astype(np.float32)
            if any(
                tensor.shape != (FFT_SIZE, WINDOW_COLUMNS)
                for tensor in (tensor_a, tensor_b, tensor_c)
            ):
                print(f"Error: incomplete window from {csv_path}.", file=sys.stderr)
                return 2
            tensor_d, cluster_metrics = clustered_tensor(
                tensor_c,
                frequencies=frequencies,
                dc_guard_hz=args.dc_guard_hz,
                threshold_db=args.cluster_threshold_db,
                minimum_pixels=args.cluster_min_pixels,
            )

            event_tag = spec["event_id"] or f"static{local_index:03d}"
            window_id = f"{csv_path.stem}_{event_tag}_c{start_column:04d}"
            relative = (
                Path(split) / gesture_class / speed / distance / f"{window_id}.npy"
            )
            path_a = out_root / "pipeline-A" / relative
            path_b = out_root / "pipeline-B" / relative
            path_c = out_root / "pipeline-C" / relative
            path_d = out_root / "pipeline-D" / relative
            if any(path.exists() for path in (path_a, path_b, path_c, path_d)):
                print(
                    f"Error: refusing to overwrite paired window {window_id}.",
                    file=sys.stderr,
                )
                return 2
            for path in (path_a, path_b, path_c, path_d):
                path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path_a, tensor_a, allow_pickle=False)
            np.save(path_b, tensor_b, allow_pickle=False)
            np.save(path_c, tensor_c, allow_pickle=False)
            np.save(path_d, tensor_d, allow_pickle=False)

            raw_start = start_column * HOP
            raw_stop = raw_start + FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP
            entry = {
                "schema_version": 3,
                "window_id": window_id,
                "pipeline_a_path": path_a.relative_to(out_root).as_posix(),
                "pipeline_b_path": path_b.relative_to(out_root).as_posix(),
                "pipeline_c_path": path_c.relative_to(out_root).as_posix(),
                "pipeline_d_path": path_d.relative_to(out_root).as_posix(),
                "source_capture": csv_path.relative_to(input_root).as_posix(),
                "source_sha256": metadata.get("data_sha256"),
                "subject_id": subject,
                "session_id": session,
                "split": split,
                "condition": gesture_class,
                "distance": metadata.get("distance"),
                "gesture_class": gesture_class,
                "speed": speed,
                "posture": context.get("posture"),
                "hand_height": context.get("hand_height"),
                "gesture_direction": metadata.get("gesture_direction"),
                "event_id": spec["event_id"],
                "repetition": spec["repetition"],
                "direction": spec["direction"],
                "window_relation": spec["window_relation"],
                "start_stft_column": start_column,
                "stop_stft_column_exclusive": stop_column,
                "source_sample_offset_start": raw_start,
                "source_sample_offset_stop_exclusive": raw_stop,
                "tensor_shape": [FFT_SIZE, WINDOW_COLUMNS],
                "tensor_dtype": "float32",
                "pipeline_a": {
                    "clutter_filter": "none",
                    "adc_center": 2048,
                    "fft_size": FFT_SIZE,
                    "hop": HOP,
                    "window": "hann",
                },
                "pipeline_b": {
                    "clutter_filter": "first-order-highpass",
                    "highpass_hz": args.highpass_hz,
                    "adc_center": 2048,
                    "fft_size": FFT_SIZE,
                    "hop": HOP,
                    "window": "hann",
                },
                "pipeline_c": {
                    "clutter_filter": "single-delay-difference",
                    "diff_shift": args.diff_shift,
                    "fft_size": FFT_SIZE,
                    "hop": HOP,
                    "window": "hann",
                    "source_capture_q15_clipped_samples": clipped_samples,
                },
                "pipeline_d": {
                    "dc_guard_hz": args.dc_guard_hz,
                    "threshold_db_above_window_noise": args.cluster_threshold_db,
                    "connectivity": 8,
                    "minimum_pixels": args.cluster_min_pixels,
                    **cluster_metrics,
                },
            }
            manifest_entries.append(entry)
            counts[(split, gesture_class, speed, distance)] += 1

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
        for entry in manifest_entries:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

    summary = {
        "schema_version": 3,
        "paired_windows": len(manifest_entries),
        "tensor_shape": [FFT_SIZE, WINDOW_COLUMNS],
        "raw_sample_span_per_window": FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP,
        "seconds_per_window": (
            FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP
        ) / SAMPLING_RATE_HZ,
        "session_split": sessions,
        "diff_shift": args.diff_shift,
        "highpass_hz": args.highpass_hz,
        "selected_classes": list(selected_classes),
        "session_context": session_context,
        "skipped_unselected_captures": skipped_unselected,
        "counts": [
            {
                "split": split,
                "gesture_class": gesture_class,
                "speed": speed,
                "distance": distance,
                "windows": count,
            }
            for (split, gesture_class, speed, distance), count in sorted(counts.items())
        ],
    }
    summary_path = out_root / "export_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(manifest_entries)} paired A-D windows.")
    print(
        f"Validated source captures: {len(validated_records)}; "
        f"skipped failed takes: {skipped_failed}; "
        f"skipped unselected takes: {skipped_unselected}"
    )
    print(f"Manifest: {manifest_path}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
