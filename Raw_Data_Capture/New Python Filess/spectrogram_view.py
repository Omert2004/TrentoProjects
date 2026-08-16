"""Compute and render a configurable spectrogram directly from raw I/Q CSV."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from raw_data import load_raw_csv, resolve_sampling_rate


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def apply_filter(
    signal: np.ndarray,
    mode: str,
    sampling_rate: float,
    cutoff_hz: float | None,
) -> np.ndarray:
    if mode == "none":
        return signal
    if mode == "mean":
        return signal - np.mean(signal)
    if mode == "difference":
        return np.diff(signal, prepend=signal[0])
    if mode == "highpass":
        if cutoff_hz is None or cutoff_hz <= 0 or cutoff_hz >= sampling_rate / 2:
            raise ValueError("highpass cutoff must be between 0 and Nyquist")
        dt = 1.0 / sampling_rate
        rc = 1.0 / (2.0 * np.pi * cutoff_hz)
        alpha = rc / (rc + dt)
        output = np.zeros_like(signal, dtype=np.complex128)
        for index in range(1, len(signal)):
            output[index] = alpha * (
                output[index - 1] + signal[index] - signal[index - 1]
            )
        return output
    raise ValueError(f"unknown filter mode: {mode}")


def compute_spectrogram(
    signal: np.ndarray,
    *,
    fft_size: int,
    hop: int,
    window_name: str,
) -> np.ndarray:
    if fft_size <= 0 or hop <= 0:
        raise ValueError("FFT size and hop must be positive")
    if len(signal) < fft_size:
        raise ValueError(f"segment has {len(signal)} samples; need at least {fft_size}")
    if window_name == "hann":
        window = np.hanning(fft_size)
    elif window_name == "rectangular":
        window = np.ones(fft_size)
    else:
        raise ValueError(f"unknown window: {window_name}")

    coherent_gain = max(float(window.sum()), np.finfo(float).tiny)
    columns = []
    for start in range(0, len(signal) - fft_size + 1, hop):
        spectrum = np.fft.fftshift(np.fft.fft(signal[start : start + fft_size] * window))
        magnitude = np.abs(spectrum) / coherent_gain
        columns.append(20.0 * np.log10(np.maximum(magnitude, np.finfo(float).tiny)))
    return np.asarray(columns, dtype=float).T


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("--sampling-rate", type=float, default=None)
    parser.add_argument("--fft-size", type=positive_int, default=256)
    parser.add_argument("--hop", type=positive_int, default=128)
    parser.add_argument("--window", choices=("hann", "rectangular"), default="hann")
    parser.add_argument(
        "--filter",
        choices=("none", "mean", "difference", "highpass"),
        default="none",
    )
    parser.add_argument("--highpass-hz", type=float, default=None)
    parser.add_argument("--center", choices=("midpoint", "mean", "none"), default="midpoint")
    parser.add_argument("--vmin", type=float, default=None)
    parser.add_argument("--vmax", type=float, default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hop > args.fft_size:
        print("Error: --hop cannot exceed --fft-size.", file=sys.stderr)
        return 2
    try:
        capture = load_raw_csv(args.input_file)
        sampling_rate = resolve_sampling_rate(capture, args.sampling_rate)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    matrices: list[np.ndarray] = []
    boundaries: list[int] = []
    total_columns = 0
    try:
        for segment_id, i_values, q_values in capture.arrays_by_segment():
            i_values = i_values.astype(float)
            q_values = q_values.astype(float)
            if args.center == "midpoint":
                i_values -= 2048.0
                q_values -= 2048.0
            elif args.center == "mean":
                i_values -= np.mean(i_values)
                q_values -= np.mean(q_values)

            signal = i_values + 1j * q_values
            signal = apply_filter(signal, args.filter, sampling_rate, args.highpass_hz)
            matrix = compute_spectrogram(
                signal,
                fft_size=args.fft_size,
                hop=args.hop,
                window_name=args.window,
            )
            if matrices:
                boundaries.append(total_columns)
            matrices.append(matrix)
            total_columns += matrix.shape[1]
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    spectrogram = np.concatenate(matrices, axis=1)
    frequencies = np.fft.fftshift(np.fft.fftfreq(args.fft_size, d=1.0 / sampling_rate))
    vmin = args.vmin if args.vmin is not None else float(np.percentile(spectrogram, 5))
    vmax = args.vmax if args.vmax is not None else float(np.percentile(spectrogram, 99.5))
    if vmax <= vmin:
        vmax = vmin + 1.0

    output_path = (
        Path(args.out)
        if args.out
        else capture.path.with_name(capture.path.stem + "_spectrogram.png")
    )
    if output_path.exists() and not args.force:
        print(f"Error: refusing to overwrite {output_path}; use --force.", file=sys.stderr)
        return 2

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=150)
    image = ax.imshow(
        spectrogram,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=(0, spectrogram.shape[1], frequencies[0], frequencies[-1]),
        cmap="inferno",
        vmin=vmin,
        vmax=vmax,
    )
    ax.axhline(0, color="cyan", linestyle="--", linewidth=0.8, alpha=0.7)
    for boundary in boundaries:
        ax.axvline(boundary, color="white", linestyle=":", linewidth=1)
    ax.set_xlabel("STFT column index (segment boundaries marked)")
    ax.set_ylabel("Doppler frequency (Hz)")
    ax.set_title(
        f"{capture.path.name}\nfs={sampling_rate:g} Hz, FFT={args.fft_size}, "
        f"hop={args.hop}, window={args.window}, filter={args.filter}"
    )
    colorbar = fig.colorbar(image, ax=ax, pad=0.02)
    colorbar.set_label("Magnitude (dB relative to one ADC code)")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved {output_path} ({spectrogram.shape[1]} STFT columns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
