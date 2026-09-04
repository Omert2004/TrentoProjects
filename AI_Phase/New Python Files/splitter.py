"""Deterministically split one validated session into contiguous STFT windows.

All windows from a source session are assigned to one dataset split. A shared
split_manifest.json prevents the same source capture from later being assigned
to a conflicting train/validation/test split.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from stft_data import load_stft_capture, metadata_path_for, sha256_file


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("output_dir")
    parser.add_argument("--split", choices=("train", "validation", "test"), required=True)
    parser.add_argument("--window-columns", type=positive_int, default=15)
    parser.add_argument("--stride-columns", type=positive_int)
    parser.add_argument("--count", type=positive_int)
    parser.add_argument("--allow-unvalidated", action="store_true")
    args = parser.parse_args()

    try:
        capture = load_stft_capture(args.input_file, require_validated=not args.allow_unvalidated)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    stride = args.stride_columns or args.window_columns
    starts = list(range(0, capture.columns.shape[0] - args.window_columns + 1, stride))
    if args.count is not None:
        starts = starts[:args.count]
    if not starts:
        print("Error: no complete window fits.", file=sys.stderr)
        return 2

    output_root = Path(args.output_dir)
    split_dir = output_root / args.split
    split_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "split_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "schema_version": 1,
        "sessions": {},
    }
    source_hash = sha256_file(capture.path)
    existing_split = manifest["sessions"].get(source_hash)
    if existing_split is not None and existing_split != args.split:
        print(
            f"Error: this source session is already assigned to {existing_split}; "
            f"refusing {args.split} to prevent leakage.",
            file=sys.stderr,
        )
        return 2

    planned = [
        split_dir / f"{capture.path.stem}_window_{number:04d}.txt"
        for number in range(len(starts))
    ]
    for path in planned:
        if path.exists() or metadata_path_for(path).exists():
            print(f"Error: refusing to overwrite {path}.", file=sys.stderr)
            return 2

    for path, start in zip(planned, starts):
        stop = start + args.window_columns
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in capture.columns[start:stop]:
                stream.write(" ".join(str(int(value)) for value in row) + "\n")
        metadata = {
            "schema_version": 3,
            "source_capture": str(capture.path),
            "source_capture_sha256": source_hash,
            "source_column_start": start,
            "source_column_stop_exclusive": stop,
            "window_columns": args.window_columns,
            "stride_columns": stride,
            "dataset_split": args.split,
            "host_transport_validation_passed": bool(
                capture.metadata and capture.metadata.get("host_transport_validation_passed")
            ),
            "scientific_sample_continuity_proven": bool(
                capture.metadata and capture.metadata.get("scientific_sample_continuity_proven")
            ),
            "columns_captured": args.window_columns,
            "data_sha256": sha256_file(path),
        }
        metadata_path_for(path).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    manifest["sessions"][source_hash] = args.split
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(planned)} deterministic {args.window_columns}-column windows "
        f"to {split_dir}; source session assigned to {args.split}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

