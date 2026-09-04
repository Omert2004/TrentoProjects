
"""
The host FFT is a tested radix-2 approximation of TI DSPLib/LEA. The Q15
input transform, quantized Hann multiplication, integer magnitude/log2, and
FFT shift match the firmware contract exactly. Because TI does not publish
every LEA intermediate rounding rule, export requires either a bit-exact
parity report or the explicit --allow-lea-approximation acknowledgement.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import numpy as np

from raw_data import load_raw_csv
from timed_pilot_capture import PILOT_ACTIONS, PILOT_CLASSES, PILOT_SPEEDS


FFT_SIZE = 256
HOP = 128
WINDOW_COLUMNS = 15
RAW_SPAN = FFT_SIZE + (WINDOW_COLUMNS - 1) * HOP
SAMPLING_RATE_HZ = 2000
DIFF_SHIFT = 4
EXPECTED_EVENTS = 5
EMPTY_WINDOWS_PER_CAPTURE = 15
DISTANCES = ("near", "mid", "far")
SPLITS = {
    "session01": "train",
    "session02": "train",
    "session03": "train",
    "session04": "validation",
    "session05": "test",
}


def positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_window(path: Path) -> np.ndarray:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"window_q15\s*\[[^]]+\]\s*=\s*\{(.*?)\};", text, re.S)
    if not match:
        raise ValueError(f"could not find window_q15 array in {path}")
    values = [int(value) for value in re.findall(r"-?\d+", match.group(1))]
    if len(values) != FFT_SIZE:
        raise ValueError(f"{path} contains {len(values)} coefficients, expected {FFT_SIZE}")
    result = np.asarray(values, dtype=np.int16)
    if np.any(result < 0):
        raise ValueError(f"{path} contains a negative Hann coefficient")
    return result


def int16_wrap(values: np.ndarray) -> np.ndarray:
    return ((values.astype(np.int64) + 32768) % 65536 - 32768).astype(np.int16)


def bit_reverse_indices(length: int) -> np.ndarray:
    bits = length.bit_length() - 1
    return np.asarray(
        [int(f"{index:0{bits}b}"[::-1], 2) for index in range(length)],
        dtype=np.int32,
    )


def q15_twiddles(size: int, length: int) -> tuple[np.ndarray, np.ndarray]:
    indices = np.arange(size // 2, dtype=np.float64) * length / size
    angles = -2.0 * np.pi * indices / length
    real = np.clip(np.rint(np.cos(angles) * 32768.0), -32768, 32767)
    imag = np.clip(np.rint(np.sin(angles) * 32768.0), -32768, 32767)
    return real.astype(np.int64), imag.astype(np.int64)


def fixed_q15_fft_batch(interleaved: np.ndarray) -> np.ndarray:
    """Fixed-scaled radix-2 FFT candidate for a batch of Q15 columns."""

    if interleaved.ndim != 3 or interleaved.shape[1:] != (FFT_SIZE, 2):
        raise ValueError("interleaved FFT input must have shape (columns, 256, 2)")
    data = interleaved[:, bit_reverse_indices(FFT_SIZE), :].astype(np.int64).copy()
    size = 2
    while size <= FFT_SIZE:
        half = size // 2
        blocks = data.reshape(data.shape[0], -1, size, 2)
        a = blocks[:, :, :half, :].copy()
        b = blocks[:, :, half:, :].copy()
        wr, wi = q15_twiddles(size, FFT_SIZE)
        wr = wr.reshape(1, 1, half)
        wi = wi.reshape(1, 1, half)
        br = b[..., 0]
        bi = b[..., 1]
        tr = (br * wr - bi * wi) >> 15
        ti = (br * wi + bi * wr) >> 15
        blocks[:, :, :half, 0] = (a[..., 0] + tr) >> 1
        blocks[:, :, :half, 1] = (a[..., 1] + ti) >> 1
        blocks[:, :, half:, 0] = (a[..., 0] - tr) >> 1
        blocks[:, :, half:, 1] = (a[..., 1] - ti) >> 1
        data = int16_wrap(data).astype(np.int64)
        size *= 2
    return data.astype(np.int16)


def fixed_q15_fft_scalar(interleaved: np.ndarray) -> np.ndarray:
    """Slow independent reference used only by --self-test-only."""

    data = interleaved[bit_reverse_indices(FFT_SIZE)].astype(np.int64).copy()
    size = 2
    while size <= FFT_SIZE:
        half = size // 2
        wr, wi = q15_twiddles(size, FFT_SIZE)
        for start in range(0, FFT_SIZE, size):
            for offset in range(half):
                br, bi = data[start + offset + half]
                tr = (br * wr[offset] - bi * wi[offset]) >> 15
                ti = (br * wi[offset] + bi * wr[offset]) >> 15
                ar, ai = data[start + offset]
                data[start + offset] = ((ar + tr) >> 1, (ai + ti) >> 1)
                data[start + offset + half] = ((ar - tr) >> 1, (ai - ti) >> 1)
        data = int16_wrap(data).astype(np.int64)
        size *= 2
    return data.astype(np.int16)


def run_self_test() -> None:
    rng = np.random.default_rng(20260819)
    values = rng.integers(-32768, 32768, size=(4, FFT_SIZE, 2), dtype=np.int16)
    batched = fixed_q15_fft_batch(values)
    for index in range(values.shape[0]):
        scalar = fixed_q15_fft_scalar(values[index])
        if not np.array_equal(batched[index], scalar):
            raise AssertionError(f"batched FFT differs from scalar reference at column {index}")


def embedded_columns(
    raw_i: np.ndarray,
    raw_q: np.ndarray,
    window_q15: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Return steady-state board-equivalent columns as uint8 [frequency,time]."""

    if raw_i.size != raw_q.size or raw_i.size < RAW_SPAN:
        raise ValueError("capture is too short for one 256x15 tensor")
    diff_i = np.diff(raw_i.astype(np.int64), prepend=int(raw_i[0]))
    diff_q = np.diff(raw_q.astype(np.int64), prepend=int(raw_q[0]))
    scaled_i = diff_i << DIFF_SHIFT
    scaled_q = diff_q << DIFF_SHIFT
    clipped = int(
        np.count_nonzero((scaled_i < -32768) | (scaled_i > 32767))
        + np.count_nonzero((scaled_q < -32768) | (scaled_q > 32767))
    )
    q15_i = np.clip(scaled_i, -32768, 32767).astype(np.int16)
    q15_q = np.clip(scaled_q, -32768, 32767).astype(np.int16)
    frames_i = np.lib.stride_tricks.sliding_window_view(q15_i, FFT_SIZE)[::HOP]
    frames_q = np.lib.stride_tricks.sliding_window_view(q15_q, FFT_SIZE)[::HOP]
    win = window_q15.astype(np.int64).reshape(1, FFT_SIZE)
    windowed_i = int16_wrap((frames_i.astype(np.int64) * win) >> 15)
    windowed_q = int16_wrap((frames_q.astype(np.int64) * win) >> 15)
    fft_input = np.stack((windowed_i, windowed_q), axis=-1)
    fft_values = fixed_q15_fft_batch(fft_input).astype(np.int64)
    magnitude = fft_values[..., 0] ** 2 + fft_values[..., 1] ** 2
    thresholds = np.left_shift(np.uint64(1), np.arange(1, 33, dtype=np.uint64))
    log2_magnitude = np.searchsorted(thresholds, magnitude.astype(np.uint64), side="right")
    columns = np.fft.fftshift(log2_magnitude.astype(np.uint8), axes=1).T
    return columns, clipped


def expected_cells() -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for gesture_class in PILOT_CLASSES:
        if gesture_class == "empty":
            result.update((gesture_class, speed, "na") for speed in PILOT_SPEEDS)
        else:
            result.update(
                (gesture_class, speed, distance)
                for speed in PILOT_SPEEDS
                for distance in DISTANCES
            )
    return result


def event_spec(event: dict[str, Any], total_columns: int, offset_ms: float) -> dict[str, Any]:
    event_start = int(event["scheduled_start_sample_offset"])
    center = event_start + offset_ms * SAMPLING_RATE_HZ / 1000.0
    start_column = int(round((center - RAW_SPAN / 2.0) / HOP))
    start_column = max(0, min(start_column, total_columns - WINDOW_COLUMNS))
    return {
        "start_column": start_column,
        "event_id": event.get("event_id"),
        "repetition": event.get("repetition"),
        "direction": event.get("direction"),
        "window_relation": "center_offset_from_scheduled_event_start",
    }


def empty_specs(total_columns: int, edge_guard_s: float) -> list[dict[str, Any]]:
    first = int(np.ceil(edge_guard_s * SAMPLING_RATE_HZ / HOP))
    last = total_columns - WINDOW_COLUMNS - first
    if last < first:
        raise ValueError("empty capture is too short after applying the edge guard")
    starts = np.rint(np.linspace(first, last, EMPTY_WINDOWS_PER_CAPTURE)).astype(int)
    if np.unique(starts).size != EMPTY_WINDOWS_PER_CAPTURE:
        raise ValueError("empty capture cannot provide 15 distinct windows")
    return [
        {
            "start_column": int(start),
            "event_id": None,
            "repetition": None,
            "direction": None,
            "window_relation": "deterministic_evenly_spaced_empty",
        }
        for start in starts
    ]


def read_parity_status(path: Path | None) -> tuple[str, dict[str, Any] | None]:
    if path is None:
        return "approximate_not_bit_exact", None
    report = json.loads(path.read_text(encoding="utf-8"))
    exact = bool(report.get("bit_exact_parity_proven"))
    if exact:
        required = {
            "sampling_rate_hz": SAMPLING_RATE_HZ,
            "fft_size": FFT_SIZE,
            "fft_hop": HOP,
            "clutter_cancel_enabled": True,
            "diff_shift": DIFF_SHIFT,
            "raw_sample_count": FFT_SIZE + 1,
        }
        mismatched = {
            key: {"expected": expected, "reported": report.get(key)}
            for key, expected in required.items()
            if report.get(key) != expected
        }
        if mismatched:
            raise ValueError(
                f"bit-exact parity report does not match this feature contract: {mismatched}"
            )
    status = "bit_exact_proven" if exact else "approximate_not_bit_exact"
    return status, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", default="dataset/model-pilot/raw/fs2000")
    parser.add_argument("--out", default="dataset/model-pilot/embedded-q15")
    parser.add_argument(
        "--window-file",
        default=None,
        help="AI_Phase/src/window_q15.c (default: resolved beside the two projects).",
    )
    parser.add_argument(
        "--event-offset-ms",
        type=float,
        default=0.0,
        help="Tensor-center offset from each scheduled action start (default: 0).",
    )
    parser.add_argument("--empty-edge-guard", type=positive_float, default=1.0)
    parser.add_argument("--parity-report", help="JSON report produced by stft_parity_check.py")
    parser.add_argument(
        "--allow-lea-approximation",
        action="store_true",
        help="Acknowledge that the Python FFT is close to, but not bit-exact with, LEA.",
    )
    parser.add_argument("--self-test-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_self_test()
        if args.self_test_only:
            print("Embedded STFT exporter self-test: PASS")
            return 0

        script_path = Path(__file__).resolve()
        default_window = script_path.parents[2] / "AI_Phase" / "src" / "window_q15.c"
        window_path = Path(args.window_file) if args.window_file else default_window
        window_q15 = load_window(window_path)
        parity_status, parity_report = read_parity_status(
            Path(args.parity_report) if args.parity_report else None
        )
        if parity_status != "bit_exact_proven" and not args.allow_lea_approximation:
            raise ValueError(
                "LEA FFT parity is not bit-exactly proven; rerun with a passing "
                "--parity-report or explicitly add --allow-lea-approximation"
            )
    except (OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    input_root = Path(args.input_root).resolve()
    out_root = Path(args.out).resolve()
    if not input_root.is_dir():
        print(f"Error: input root not found: {input_root}", file=sys.stderr)
        return 2
    if out_root.exists() and any(out_root.iterdir()):
        print(f"Error: refusing to overwrite non-empty output directory: {out_root}", file=sys.stderr)
        return 2

    records: dict[tuple[str, str, str, str, str], tuple[Path, dict[str, Any]]] = {}
    ignored_unassigned = 0
    try:
        for metadata_path in sorted(input_root.rglob("*.metadata.json")):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            session = str(metadata.get("session_id", "")).lower()
            if session not in SPLITS:
                ignored_unassigned += 1
                continue
            if not metadata.get("host_transport_validation_passed", False):
                raise ValueError(f"failed capture exists inside a main session: {metadata_path}")
            if float(metadata.get("configured_sampling_rate_hz", 0)) != SAMPLING_RATE_HZ:
                raise ValueError(f"non-2 kHz source capture: {metadata_path}")
            gesture_class = str(metadata.get("gesture_class") or metadata.get("condition", ""))
            speed = str(metadata.get("speed", ""))
            distance = str(metadata.get("distance") or "na")
            subject = str(metadata.get("subject_id", "")).lower()
            if gesture_class not in PILOT_CLASSES or speed not in PILOT_SPEEDS:
                raise ValueError(f"invalid class/speed metadata: {metadata_path}")
            key = (subject, session, gesture_class, speed, distance)
            if key in records:
                raise ValueError(f"duplicate validated capture for {key}")
            records[key] = (metadata_path, metadata)

        for subject_session in sorted({(key[0], key[1]) for key in records}):
            cells = {
                (gesture_class, speed, distance)
                for (subject, session, gesture_class, speed, distance) in records
                if (subject, session) == subject_session
            }
            if cells != expected_cells():
                raise ValueError(
                    f"incomplete matrix for {subject_session}: "
                    f"missing={sorted(expected_cells() - cells)}, extra={sorted(cells - expected_cells())}"
                )
        represented = {key[1] for key in records}
        if represented != set(SPLITS):
            raise ValueError(f"expected sessions {sorted(SPLITS)}, found {sorted(represented)}")

        tensors: list[np.ndarray] = []
        entries: list[dict[str, Any]] = []
        tensor_paths: set[str] = set()
        counts: Counter[tuple[str, str]] = Counter()
        total_clipped = 0
        for (subject, session, gesture_class, speed, distance), (metadata_path, metadata) in sorted(records.items()):
            csv_path = metadata_path.with_name(str(metadata["capture_csv"]))
            expected_hash = str(metadata.get("data_sha256", "")).lower()
            actual_hash = sha256_file(csv_path)
            if expected_hash and actual_hash != expected_hash:
                raise ValueError(f"SHA256 mismatch: {csv_path}")
            capture = load_raw_csv(csv_path)
            columns, clipped = embedded_columns(capture.i, capture.q, window_q15)
            total_clipped += clipped
            events = metadata.get("event_markers") or []
            if gesture_class in PILOT_ACTIONS:
                if len(events) != EXPECTED_EVENTS:
                    raise ValueError(f"expected 5 event markers: {metadata_path}")
                specs = [event_spec(event, columns.shape[1], args.event_offset_ms) for event in events]
            else:
                if events:
                    raise ValueError(f"empty capture contains event markers: {metadata_path}")
                specs = empty_specs(columns.shape[1], args.empty_edge_guard)

            split = SPLITS[session]
            for local_index, spec in enumerate(specs, start=1):
                start = int(spec["start_column"])
                tensor = columns[:, start : start + WINDOW_COLUMNS].copy()
                if tensor.shape != (FFT_SIZE, WINDOW_COLUMNS) or tensor.dtype != np.uint8:
                    raise ValueError(f"invalid tensor from {csv_path} at column {start}")
                event_tag = spec["event_id"] or f"static{local_index:02d}"
                # Keep ZIP extraction paths short. Full source filename,
                # timestamp, hash, subject, and STFT-column provenance remain
                # available in the manifest entry below.
                window_id = (
                    f"{session}_{gesture_class}_{speed}_{distance}_{event_tag}"
                )
                relative = Path(split) / gesture_class / speed / distance / f"{window_id}.npy"
                relative_key = relative.as_posix()
                if relative_key in tensor_paths:
                    raise ValueError(
                        "short tensor filename collision; this naming contract "
                        f"supports one subject per session: {relative_key}"
                    )
                tensor_paths.add(relative_key)
                raw_start = start * HOP
                entry = {
                    "schema_version": 1,
                    "window_id": window_id,
                    "tensor_path": relative_key,
                    "source_capture": csv_path.relative_to(input_root).as_posix(),
                    "source_sha256": actual_hash,
                    "subject_id": subject,
                    "session_id": session,
                    "split": split,
                    "gesture_class": gesture_class,
                    "speed": speed,
                    "distance": None if distance == "na" else distance,
                    "event_id": spec["event_id"],
                    "repetition": spec["repetition"],
                    "direction": spec["direction"],
                    "window_relation": spec["window_relation"],
                    "event_offset_ms": args.event_offset_ms if spec["event_id"] else None,
                    "start_stft_column": start,
                    "source_sample_offset_start": raw_start,
                    "source_sample_offset_stop_exclusive": raw_start + RAW_SPAN,
                    "tensor_shape": [FFT_SIZE, WINDOW_COLUMNS],
                    "tensor_dtype": "uint8",
                    "board_native_shape": [WINDOW_COLUMNS, FFT_SIZE],
                    "board_relation": "host tensor is transpose of AI_Phase spectrogram",
                    "capture_q15_clipped_components": clipped,
                }
                tensors.append(tensor)
                entries.append(entry)
                counts[(split, gesture_class)] += 1

        expected_counts = {
            ("train", gesture_class): 135 for gesture_class in PILOT_CLASSES
        } | {
            ("validation", gesture_class): 45 for gesture_class in PILOT_CLASSES
        } | {
            ("test", gesture_class): 45 for gesture_class in PILOT_CLASSES
        }
        if dict(counts) != expected_counts:
            raise ValueError(f"unexpected class/split counts: {dict(sorted(counts.items()))}")

        out_root.mkdir(parents=True, exist_ok=True)
        for tensor, entry in zip(tensors, entries):
            path = out_root / entry["tensor_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, tensor, allow_pickle=False)
        manifest_path = out_root / "embedded_windows_manifest.jsonl"
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            for entry in entries:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

        summary = {
            "schema_version": 1,
            "feature_contract": {
                "sampling_rate_hz": SAMPLING_RATE_HZ,
                "clutter_filter": "single-delay-first-difference",
                "diff_shift": DIFF_SHIFT,
                "q15_saturation": True,
                "fft_size": FFT_SIZE,
                "hop": HOP,
                "window": "quantized-q15-hann-from-AI_Phase",
                "fft_scaling": "fixed FFT/N",
                "magnitude": "integer magnitude-squared",
                "compression": "floor(log2(magnitude)), zero maps to zero",
                "frequency_order": "fftshift",
                "host_tensor_shape": [FFT_SIZE, WINDOW_COLUMNS],
                "board_tensor_shape": [WINDOW_COLUMNS, FFT_SIZE],
            },
            "lea_fft_emulation_status": parity_status,
            "parity_report": parity_report,
            "warning": (
                None if parity_status == "bit_exact_proven" else
                "TI LEA internal FFT rounding is approximated; do not claim bit-exact parity."
            ),
            "event_offset_ms": args.event_offset_ms,
            "filename_format": (
                "<session>_<class>_<speed>_<distance>_<event-or-static>.npy"
            ),
            "empty_windows_per_capture": EMPTY_WINDOWS_PER_CAPTURE,
            "split_by_session": SPLITS,
            "captures": len(records),
            "tensors": len(entries),
            "total_q15_clipped_components": total_clipped,
            "ignored_unassigned_captures": ignored_unassigned,
            "counts": [
                {"split": split, "gesture_class": gesture_class, "tensors": count}
                for (split, gesture_class), count in sorted(counts.items())
            ],
        }
        summary_path = out_root / "embedded_export_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("Embedded-style dataset export: PASS")
    print(f"Validated captures: {len(records)}")
    print(f"Exported tensors:  {len(entries)} (225 per class)")
    print(f"Q15 clipped components: {total_clipped}")
    print(f"LEA FFT status: {parity_status}")
    print(f"Output:   {out_root}")
    print(f"Manifest: {manifest_path}")
    print(f"Summary:  {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
