"""Strict loading helpers for validated AI_Phase spectrogram captures."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from stft_protocol import COLUMN_MAX, COLUMN_MIN, COLUMN_SIZE


def metadata_path_for(path: str | Path) -> Path:
    source = Path(path)
    return source.with_suffix(".metadata.json")


@dataclass(frozen=True)
class StftCapture:
    path: Path
    columns: np.ndarray  # shape: time columns x frequency bins
    metadata: dict | None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_stft_capture(path: str | Path, *, require_validated: bool = True) -> StftCapture:
    source = Path(path)
    rows: list[list[int]] = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                values = [int(value) for value in line.split()]
            except ValueError as exc:
                raise ValueError(f"{source}: line {line_number} contains a non-integer") from exc
            if len(values) != COLUMN_SIZE:
                raise ValueError(
                    f"{source}: line {line_number} has {len(values)} values, expected {COLUMN_SIZE}"
                )
            if any(value < COLUMN_MIN or value > COLUMN_MAX for value in values):
                raise ValueError(f"{source}: line {line_number} contains a value outside 0..31")
            rows.append(values)
    if not rows:
        raise ValueError(f"{source}: no complete columns")

    meta_path = metadata_path_for(source)
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    if require_validated:
        if metadata is None:
            raise ValueError(f"{source}: validated metadata sidecar is missing")
        if not metadata.get("host_transport_validation_passed", False):
            raise ValueError(f"{source}: capture metadata says transport validation failed")
        expected_hash = metadata.get("data_sha256")
        if not expected_hash:
            raise ValueError(f"{source}: metadata has no data_sha256")
        if sha256_file(source) != expected_hash:
            raise ValueError(f"{source}: SHA-256 does not match metadata")
        expected_rows = metadata.get("columns_captured")
        if expected_rows != len(rows):
            raise ValueError(
                f"{source}: metadata says {expected_rows} columns but file contains {len(rows)}"
            )

    return StftCapture(source, np.asarray(rows, dtype=np.uint8), metadata)
