"""Deterministically split a validated raw I/Q CSV into contiguous windows.

Windows never cross a segment boundary and are never chosen randomly. This
avoids unreproducible datasets and makes overlap explicit through ``--stride``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from raw_data import load_raw_csv, metadata_path_for, resolve_sampling_rate, write_raw_csv


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be numeric") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("output_dir")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--window-samples", type=positive_int)
    group.add_argument("--window-seconds", type=positive_float)
    parser.add_argument(
        "--stride-samples",
        type=positive_int,
        default=None,
        help="Start-to-start spacing; defaults to non-overlapping windows.",
    )
    parser.add_argument("--count", type=positive_int, default=None)
    parser.add_argument(
        "--sampling-rate",
        type=positive_float,
        default=None,
        help="Required only when metadata is absent; must agree when metadata exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        capture = load_raw_csv(args.input_file)
        sampling_rate = resolve_sampling_rate(capture, args.sampling_rate)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    window_samples = args.window_samples
    if window_samples is None:
        window_samples = int(round(args.window_seconds * sampling_rate))
    if window_samples <= 0:
        print("Error: requested window rounds to zero samples.", file=sys.stderr)
        return 2
    stride = args.stride_samples or window_samples

    candidates: list[tuple[int, int, int]] = []
    # Tuples are (segment id, global array start, global array stop).
    for segment_id in capture.segment_ids():
        positions = [
            index for index, value in enumerate(capture.segment) if int(value) == segment_id
        ]
        if not positions:
            continue
        segment_start = positions[0]
        segment_stop = positions[-1] + 1
        start = segment_start
        while start + window_samples <= segment_stop:
            candidates.append((segment_id, start, start + window_samples))
            start += stride

    if args.count is not None:
        candidates = candidates[: args.count]
    if not candidates:
        print("Error: no complete windows fit inside any segment.", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    planned_paths = [
        output_dir / f"{capture.path.stem}_window_{window_number:04d}.csv"
        for window_number in range(len(candidates))
    ]
    conflicts = [
        path
        for output_path in planned_paths
        for path in (output_path, metadata_path_for(output_path))
        if path.exists()
    ]
    if conflicts:
        print(
            f"Error: refusing to overwrite existing output {conflicts[0]}",
            file=sys.stderr,
        )
        return 2

    for output_path, (source_segment, start, stop) in zip(planned_paths, candidates):
        rows = (
            (
                local_index,
                0,
                int(capture.i[source_index]),
                int(capture.q[source_index]),
            )
            for local_index, source_index in enumerate(range(start, stop))
        )
        write_raw_csv(output_path, rows)

        metadata = {
            "schema_version": 2,
            "configured_sampling_rate_hz": sampling_rate,
            "source_capture": str(capture.path),
            "source_segment": source_segment,
            "source_sample_idx_start": int(capture.sample_idx[start]),
            "source_sample_idx_stop_exclusive": int(capture.sample_idx[stop - 1]) + 1,
            "window_samples": window_samples,
            "stride_samples": stride,
            "host_transport_validation_passed": bool(
                capture.metadata
                and capture.metadata.get("host_transport_validation_passed", False)
            ),
            "scientific_sample_continuity_proven": bool(
                capture.metadata
                and capture.metadata.get("scientific_sample_continuity_proven", False)
            ),
        }
        output_metadata_path = metadata_path_for(output_path)
        output_metadata_path.write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )

    print(
        f"Wrote {len(candidates)} deterministic window(s), "
        f"{window_samples} samples each, stride {stride}, to {output_dir}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
