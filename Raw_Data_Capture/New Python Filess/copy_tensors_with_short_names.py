"""Copy an embedded-Q15 tensor dataset into a short, flat filename layout.

The source dataset is never modified. Each tensor is copied byte-for-byte to:

    <output>/<split>/<session>_<class>_<speed>_<distance>_<event>.npy

The copied JSONL manifest is updated to point at the new tensor paths while
retaining the original paths and window IDs for provenance.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any


MANIFEST_NAME = "embedded_windows_manifest.jsonl"
SUMMARY_NAME = "embedded_export_summary.json"
SAFE_PART = re.compile(r"^[A-Za-z0-9_-]+$")
STATIC_TAG = re.compile(r"(?:^|_)(static\d+)(?:_|$)", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_part(value: Any, field: str) -> str:
    text = str(value).strip().lower()
    if not text or not SAFE_PART.fullmatch(text):
        raise ValueError(f"unsafe or missing {field}: {value!r}")
    return text


def read_manifest(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}: invalid JSON on line {line_number}: {exc}"
                ) from exc
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: line {line_number} is not an object")
            entries.append(entry)
    if not entries:
        raise ValueError(f"manifest is empty: {path}")
    return entries


def static_tag(entry: dict[str, Any], fallback_number: int) -> str:
    for value in (entry.get("window_id"), entry.get("tensor_path")):
        match = STATIC_TAG.search(str(value or ""))
        if match:
            number = int(re.search(r"\d+", match.group(1)).group())
            return f"static{number:02d}"
    return f"static{fallback_number:02d}"


def short_identity(
    entry: dict[str, Any],
    static_number: int,
) -> tuple[str, str, str, str, str, str]:
    split = safe_part(entry.get("split"), "split")
    session = safe_part(entry.get("session_id"), "session_id")
    gesture_class = safe_part(entry.get("gesture_class"), "gesture_class")
    speed = safe_part(entry.get("speed"), "speed")
    distance_value = entry.get("distance")
    distance = "na" if distance_value in (None, "", "na") else safe_part(
        distance_value, "distance"
    )
    event_value = entry.get("event_id")
    event = (
        safe_part(event_value, "event_id")
        if event_value not in (None, "")
        else static_tag(entry, static_number)
    )
    return split, session, gesture_class, speed, distance, event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        help="Existing embedded-q15-offsetNNN dataset directory.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="New directory to create; it must not already exist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = Path(args.source).resolve()
    output_root = Path(args.output).resolve()
    manifest_path = source_root / MANIFEST_NAME
    summary_path = source_root / SUMMARY_NAME

    if not source_root.is_dir():
        print(f"Error: source directory does not exist: {source_root}", file=sys.stderr)
        return 2
    if not manifest_path.is_file():
        print(f"Error: source manifest is missing: {manifest_path}", file=sys.stderr)
        return 2
    if output_root.exists():
        print(
            f"Error: output already exists; refusing to mix datasets: {output_root}",
            file=sys.stderr,
        )
        return 2
    if output_root == source_root or source_root in output_root.parents:
        print("Error: output must not be inside the source dataset.", file=sys.stderr)
        return 2

    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}-building-", dir=output_root.parent)
    )

    try:
        entries = read_manifest(manifest_path)
        copied_entries: list[dict[str, Any]] = []
        used_destinations: set[str] = set()
        static_counts: defaultdict[tuple[str, str, str, str], int] = defaultdict(int)
        counts: Counter[tuple[str, str]] = Counter()
        longest_name = 0
        total_bytes = 0

        source_resolved = source_root.resolve()
        for entry_number, entry in enumerate(entries, start=1):
            old_relative = Path(str(entry.get("tensor_path", "")))
            if old_relative.is_absolute() or ".." in old_relative.parts:
                raise ValueError(
                    f"unsafe tensor_path in manifest entry {entry_number}: {old_relative}"
                )
            source_tensor = (source_root / old_relative).resolve()
            if not source_tensor.is_relative_to(source_resolved):
                raise ValueError(f"tensor escapes source root: {source_tensor}")
            if not source_tensor.is_file():
                raise ValueError(f"source tensor is missing: {source_tensor}")
            if source_tensor.suffix.lower() != ".npy":
                raise ValueError(f"source tensor is not an .npy file: {source_tensor}")

            base_key = (
                str(entry.get("session_id")),
                str(entry.get("gesture_class")),
                str(entry.get("speed")),
                str(entry.get("distance") or "na"),
            )
            if entry.get("event_id") in (None, ""):
                static_counts[base_key] += 1
            split, session, gesture_class, speed, distance, event = short_identity(
                entry, static_counts[base_key]
            )
            filename = (
                f"{session}_{gesture_class}_{speed}_{distance}_{event}.npy"
            )
            new_relative = Path(split) / filename
            new_key = new_relative.as_posix()
            if new_key in used_destinations:
                raise ValueError(
                    "short-name collision. This layout requires unique "
                    f"session/class/speed/distance/event identities: {new_key}"
                )
            used_destinations.add(new_key)

            destination = temporary_root / new_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_tensor, destination)
            source_hash = sha256_file(source_tensor)
            destination_hash = sha256_file(destination)
            if source_hash != destination_hash:
                raise ValueError(f"copy verification failed: {source_tensor}")

            copied = dict(entry)
            copied["original_window_id"] = entry.get("window_id")
            copied["original_tensor_path"] = old_relative.as_posix()
            copied["window_id"] = Path(filename).stem
            copied["tensor_path"] = new_key
            copied["tensor_file_sha256"] = destination_hash
            copied_entries.append(copied)
            counts[(split, gesture_class)] += 1
            longest_name = max(longest_name, len(filename))
            total_bytes += destination.stat().st_size

        output_manifest = temporary_root / MANIFEST_NAME
        with output_manifest.open("x", encoding="utf-8", newline="\n") as stream:
            for entry in copied_entries:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            if not isinstance(summary, dict):
                raise ValueError(f"summary root is not an object: {summary_path}")
        else:
            summary = {}
        summary["short_filename_copy"] = {
            "source_directory_name": source_root.name,
            "source_dataset_modified": False,
            "layout": "flat tensors within each split directory",
            "filename_format": (
                "<session>_<class>_<speed>_<distance>_<event-or-static>.npy"
            ),
            "tensor_files": len(copied_entries),
            "longest_filename_characters": longest_name,
            "all_copies_sha256_verified": True,
            "total_tensor_bytes": total_bytes,
            "counts": [
                {"split": split, "gesture_class": label, "tensors": count}
                for (split, label), count in sorted(counts.items())
            ],
        }
        (temporary_root / SUMMARY_NAME).write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )

        readme = (
            "SHORT-FILENAME TENSOR COPY\n\n"
            f"Source folder: {source_root.name}\n"
            "The source dataset was not modified. Tensor bytes were copied "
            "and SHA-256 verified.\n"
            "Layout: <split>/<session>_<class>_<speed>_<distance>_"
            "<event-or-static>.npy\n"
            "Full original paths and window IDs remain in "
            f"{MANIFEST_NAME}.\n"
        )
        (temporary_root / "SHORT_FILENAME_COPY_README.txt").write_text(
            readme, encoding="utf-8"
        )

        temporary_root.replace(output_root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        shutil.rmtree(temporary_root, ignore_errors=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("Short-filename tensor copy: PASS")
    print(f"Source left unchanged: {source_root}")
    print(f"New dataset folder:    {output_root}")
    print(f"Tensor files copied:   {len(copied_entries)}")
    print(f"Longest filename:      {longest_name} characters")
    print("Every tensor copy SHA-256 verified: YES")
    for (split, label), count in sorted(counts.items()):
        print(f"  {split:10s} {label:28s} {count:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
