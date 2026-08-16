"""Strict loading and writing helpers for raw I/Q capture CSV files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np


REQUIRED_COLUMNS = ("sample_idx", "segment", "I", "Q")
ADC_MIN = 0
ADC_MAX = 4095


@dataclass(frozen=True)
class RawCapture:
    path: Path
    sample_idx: np.ndarray
    segment: np.ndarray
    i: np.ndarray
    q: np.ndarray
    metadata: dict[str, Any] | None

    def segment_ids(self) -> list[int]:
        ordered: list[int] = []
        seen: set[int] = set()
        for value in self.segment:
            segment_id = int(value)
            if segment_id not in seen:
                seen.add(segment_id)
                ordered.append(segment_id)
        return ordered

    def arrays_by_segment(self) -> list[tuple[int, np.ndarray, np.ndarray]]:
        return [
            (segment_id, self.i[self.segment == segment_id], self.q[self.segment == segment_id])
            for segment_id in self.segment_ids()
        ]


def metadata_path_for(csv_path: Path) -> Path:
    return csv_path.with_suffix(".metadata.json")


def load_metadata(csv_path: Path) -> dict[str, Any] | None:
    metadata_path = metadata_path_for(csv_path)
    if not metadata_path.exists():
        return None
    try:
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{metadata_path}: invalid metadata JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{metadata_path}: metadata root must be an object")
    return value


def load_raw_csv(path: str | Path, *, require_contiguous: bool = True) -> RawCapture:
    csv_path = Path(path)
    rows: list[tuple[int, int, int, int]] = []

    try:
        stream = csv_path.open("r", newline="", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{csv_path}: cannot open file: {exc}") from exc

    with stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path}: missing CSV header")
        missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise ValueError(f"{csv_path}: missing columns: {', '.join(missing)}")

        for line_number, row in enumerate(reader, start=2):
            try:
                sample_idx = int(row["sample_idx"])
                segment = int(row["segment"])
                i_value = int(row["I"])
                q_value = int(row["Q"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{csv_path}: line {line_number} contains a non-integer field"
                ) from exc

            if sample_idx < 0 or segment < 0:
                raise ValueError(
                    f"{csv_path}: line {line_number} has a negative index or segment"
                )
            if not (ADC_MIN <= i_value <= ADC_MAX and ADC_MIN <= q_value <= ADC_MAX):
                raise ValueError(
                    f"{csv_path}: line {line_number} has an out-of-range 12-bit ADC value "
                    f"(I={i_value}, Q={q_value})"
                )
            rows.append((sample_idx, segment, i_value, q_value))

    if not rows:
        raise ValueError(f"{csv_path}: no I/Q rows found")

    values = np.asarray(rows, dtype=np.int64)
    indices = values[:, 0]
    segments = values[:, 1]

    if require_contiguous:
        deltas = np.diff(indices)
        bad = np.flatnonzero(deltas != 1)
        if bad.size:
            row = int(bad[0]) + 2
            raise ValueError(
                f"{csv_path}: sample_idx is discontinuous between CSV lines "
                f"{row} and {row + 1} ({indices[bad[0]]} -> {indices[bad[0] + 1]})"
            )

    # A segment must occupy one contiguous run; returning to an earlier segment
    # would make time ordering ambiguous.
    seen: set[int] = set()
    last_segment: int | None = None
    for segment in segments:
        segment_int = int(segment)
        if segment_int != last_segment:
            if segment_int in seen:
                raise ValueError(f"{csv_path}: segment {segment_int} appears in multiple runs")
            seen.add(segment_int)
            last_segment = segment_int

    return RawCapture(
        path=csv_path,
        sample_idx=indices,
        segment=segments,
        i=values[:, 2],
        q=values[:, 3],
        metadata=load_metadata(csv_path),
    )


def resolve_sampling_rate(capture: RawCapture, cli_rate: float | None) -> float:
    metadata_rate: float | None = None
    if capture.metadata is not None:
        raw_value = capture.metadata.get("configured_sampling_rate_hz")
        if raw_value is not None:
            try:
                metadata_rate = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("metadata configured_sampling_rate_hz is not numeric") from exc

    if cli_rate is None and metadata_rate is None:
        raise ValueError(
            "sampling rate is unknown; provide --sampling-rate or use a capture with metadata"
        )
    if cli_rate is not None and cli_rate <= 0:
        raise ValueError("sampling rate must be positive")
    if metadata_rate is not None and metadata_rate <= 0:
        raise ValueError("metadata sampling rate must be positive")
    if cli_rate is not None and metadata_rate is not None:
        if abs(cli_rate - metadata_rate) > max(1e-9, metadata_rate * 1e-6):
            raise ValueError(
                f"--sampling-rate {cli_rate:g} disagrees with metadata rate "
                f"{metadata_rate:g} Hz"
            )
    return float(cli_rate if cli_rate is not None else metadata_rate)


def write_raw_csv(
    path: str | Path,
    rows: Iterable[tuple[int, int, int, int]],
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(REQUIRED_COLUMNS)
        writer.writerows(rows)
