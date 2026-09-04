"""Apply one clutter candidate and optional STFT clustering to raw I/Q data.

This is an offline comparison tool.  It deliberately reuses the same raw CSV
for every candidate so that scene and hand motion are not changed between
filters.  ``--diff-shift`` emulates the input scaling and Q15 saturation used
by the on-chip single-delay canceller; it is a gain setting, not a separate
clutter filter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from raw_data import load_raw_csv, resolve_sampling_rate
from spectrogram_view import apply_filter, compute_spectrogram, positive_int


def nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def emulate_difference_q15(signal: np.ndarray, shift: int) -> tuple[np.ndarray, int]:
    difference = np.diff(signal, prepend=signal[0])
    scale = float(1 << shift)
    scaled_i = np.real(difference) * scale
    scaled_q = np.imag(difference) * scale
    clipped = int(
        np.count_nonzero(
            (scaled_i > 32767)
            | (scaled_i < -32768)
            | (scaled_q > 32767)
            | (scaled_q < -32768)
        )
    )
    q15_i = np.clip(scaled_i, -32768, 32767)
    q15_q = np.clip(scaled_q, -32768, 32767)
    return q15_i + 1j * q15_q, clipped


def connected_component_filter(mask: np.ndarray, minimum_pixels: int) -> tuple[np.ndarray, int, int]:
    """Keep 8-connected components with at least ``minimum_pixels`` cells."""

    rows, columns = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    kept = np.zeros_like(mask, dtype=bool)
    kept_components = 0
    removed_components = 0

    for row in range(rows):
        for column in range(columns):
            if not mask[row, column] or visited[row, column]:
                continue
            stack = [(row, column)]
            visited[row, column] = True
            component: list[tuple[int, int]] = []
            while stack:
                current_row, current_column = stack.pop()
                component.append((current_row, current_column))
                for row_delta in (-1, 0, 1):
                    for column_delta in (-1, 0, 1):
                        if row_delta == 0 and column_delta == 0:
                            continue
                        next_row = current_row + row_delta
                        next_column = current_column + column_delta
                        if not (0 <= next_row < rows and 0 <= next_column < columns):
                            continue
                        if mask[next_row, next_column] and not visited[next_row, next_column]:
                            visited[next_row, next_column] = True
                            stack.append((next_row, next_column))
            if len(component) >= minimum_pixels:
                kept_components += 1
                for component_row, component_column in component:
                    kept[component_row, component_column] = True
            else:
                removed_components += 1
    return kept, kept_components, removed_components


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("--sampling-rate", type=positive_float, default=None)
    parser.add_argument(
        "--clutter-filter",
        choices=("none", "mean", "difference", "highpass"),
        required=True,
    )
    parser.add_argument("--highpass-hz", type=positive_float, default=None)
    parser.add_argument("--diff-shift", type=nonnegative_int, default=None)
    parser.add_argument("--fft-size", type=positive_int, default=256)
    parser.add_argument("--hop", type=positive_int, default=128)
    parser.add_argument("--window", choices=("hann", "rectangular"), default="hann")
    parser.add_argument("--dc-guard-hz", type=positive_float, default=20.0)
    parser.add_argument("--cluster-threshold-db", type=positive_float, default=9.0)
    parser.add_argument("--cluster-min-pixels", type=positive_int, default=4)
    parser.add_argument("--out-dir", default="filter-results")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hop > args.fft_size:
        print("Error: --hop cannot exceed --fft-size.", file=sys.stderr)
        return 2
    if args.clutter_filter == "difference" and args.diff_shift is None:
        print("Error: difference requires --diff-shift.", file=sys.stderr)
        return 2
    if args.clutter_filter != "difference" and args.diff_shift is not None:
        print("Error: --diff-shift is only valid with difference.", file=sys.stderr)
        return 2
    if args.clutter_filter == "highpass" and args.highpass_hz is None:
        print("Error: highpass requires --highpass-hz.", file=sys.stderr)
        return 2
    if args.clutter_filter != "highpass" and args.highpass_hz is not None:
        print("Error: --highpass-hz is only valid with highpass.", file=sys.stderr)
        return 2

    try:
        capture = load_raw_csv(args.input_file)
        sampling_rate = resolve_sampling_rate(capture, args.sampling_rate)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    matrices: list[np.ndarray] = []
    clipped_samples = 0
    total_samples = 0
    try:
        for _segment_id, i_values, q_values in capture.arrays_by_segment():
            raw_signal = i_values.astype(float) + 1j * q_values.astype(float)
            total_samples += len(raw_signal)
            if args.clutter_filter == "difference":
                filtered, clipped = emulate_difference_q15(raw_signal, args.diff_shift)
                clipped_samples += clipped
            else:
                centered = raw_signal - (2048.0 + 1j * 2048.0)
                filtered = apply_filter(
                    centered,
                    args.clutter_filter,
                    sampling_rate,
                    args.highpass_hz,
                )
            matrices.append(
                compute_spectrogram(
                    filtered,
                    fft_size=args.fft_size,
                    hop=args.hop,
                    window_name=args.window,
                )
            )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    spectrogram = np.concatenate(matrices, axis=1)
    frequencies = np.fft.fftshift(
        np.fft.fftfreq(args.fft_size, d=1.0 / sampling_rate)
    )
    outside_dc = np.abs(frequencies) > args.dc_guard_hz
    noise_floor_db = float(np.median(spectrogram[outside_dc, :]))
    threshold_db = noise_floor_db + args.cluster_threshold_db
    candidate_mask = spectrogram >= threshold_db
    candidate_mask[~outside_dc, :] = False
    clustered_mask, kept_components, removed_components = connected_component_filter(
        candidate_mask,
        args.cluster_min_pixels,
    )

    candidate_name = args.clutter_filter
    if args.clutter_filter == "difference":
        candidate_name += f"-shift{args.diff_shift}"
    elif args.clutter_filter == "highpass":
        candidate_name += f"-{args.highpass_hz:g}hz"
    stem = f"{capture.path.stem}_{candidate_name}_cluster{args.cluster_min_pixels}"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / f"{stem}.png"
    metrics_path = out_dir / f"{stem}.metrics.json"
    if not args.force and (image_path.exists() or metrics_path.exists()):
        print(f"Error: refusing to overwrite {image_path} or {metrics_path}; use --force.", file=sys.stderr)
        return 2

    clip_percent = 100.0 * clipped_samples / max(total_samples, 1)
    metrics = {
        "schema_version": 1,
        "source_capture": str(capture.path),
        "configured_sampling_rate_hz": sampling_rate,
        "clutter_filter": args.clutter_filter,
        "highpass_hz": args.highpass_hz,
        "diff_shift": args.diff_shift,
        "q15_clipped_samples": clipped_samples,
        "q15_clipped_sample_percent": clip_percent,
        "fft_size": args.fft_size,
        "hop": args.hop,
        "window": args.window,
        "dc_guard_hz": args.dc_guard_hz,
        "noise_floor_db": noise_floor_db,
        "cluster_threshold_db_above_noise": args.cluster_threshold_db,
        "cluster_absolute_threshold_db": threshold_db,
        "cluster_min_pixels": args.cluster_min_pixels,
        "candidate_pixels": int(np.count_nonzero(candidate_mask)),
        "kept_pixels": int(np.count_nonzero(clustered_mask)),
        "kept_components": kept_components,
        "removed_components": removed_components,
    }
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    vmin = float(np.percentile(spectrogram, 5))
    vmax = float(np.percentile(spectrogram, 99.5))
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), dpi=150, sharex=True)
    extent = (0, spectrogram.shape[1], frequencies[0], frequencies[-1])
    image = axes[0].imshow(
        spectrogram,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=extent,
        cmap="inferno",
        vmin=vmin,
        vmax=vmax,
    )
    axes[0].set_title(f"{candidate_name}: filtered STFT")
    fig.colorbar(image, ax=axes[0], pad=0.01, label="dB")
    axes[1].imshow(candidate_mask, origin="lower", aspect="auto", interpolation="nearest", extent=extent)
    axes[1].set_title(f"Threshold mask: noise floor + {args.cluster_threshold_db:g} dB")
    axes[2].imshow(clustered_mask, origin="lower", aspect="auto", interpolation="nearest", extent=extent)
    axes[2].set_title(f"8-connected components, minimum {args.cluster_min_pixels} pixels")
    for axis in axes:
        axis.set_ylabel("Doppler (Hz)")
        axis.axhline(0, color="cyan", linestyle="--", linewidth=0.6)
    axes[-1].set_xlabel("STFT column index")
    fig.suptitle(capture.path.name)
    fig.tight_layout()
    fig.savefig(image_path)
    plt.close(fig)

    print(f"Saved {image_path}")
    print(f"Saved {metrics_path}")
    if args.clutter_filter == "difference":
        print(f"Q15 clipping: {clipped_samples}/{total_samples} samples ({clip_percent:.4f}%)")
    print(
        f"Clustering: kept {kept_components} component(s), "
        f"removed {removed_components}; kept pixels={metrics['kept_pixels']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
