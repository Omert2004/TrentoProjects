"""Estimate stationary interference with an averaged complex-I/Q Welch PSD.

Each capture segment is processed independently, so UART-drain gaps are never
treated as uniformly sampled data. Results are heuristic evidence, not an
automatic proof that a particular interference source is present or absent.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from raw_data import RawCapture, load_raw_csv, resolve_sampling_rate


def positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be numeric") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def centered_segments(capture: RawCapture, mode: str) -> list[np.ndarray]:
    output = []
    for _segment_id, i_values, q_values in capture.arrays_by_segment():
        i_float = i_values.astype(float)
        q_float = q_values.astype(float)
        if mode == "midpoint":
            i_float -= 2048.0
            q_float -= 2048.0
        elif mode == "mean":
            i_float -= np.mean(i_float)
            q_float -= np.mean(q_float)
        output.append(i_float + 1j * q_float)
    return output


def welch_complex(
    segments: list[np.ndarray],
    *,
    sampling_rate: float,
    requested_nperseg: int,
    overlap_percent: float,
) -> tuple[np.ndarray, np.ndarray, int, int]:
    longest = max((len(segment) for segment in segments), default=0)
    usable = min(requested_nperseg, longest)
    if usable < 8:
        raise ValueError("need at least 8 samples in one continuous segment")

    # A power-of-two length makes frequency placement and reproducibility clear.
    nperseg = 1 << (usable.bit_length() - 1)
    overlap_samples = int(round(nperseg * overlap_percent / 100.0))
    step = nperseg - overlap_samples
    if step <= 0:
        raise ValueError("overlap leaves a nonpositive Welch step")

    window = np.hanning(nperseg)
    scale = sampling_rate * float(np.sum(window * window))
    power_sum = np.zeros(nperseg, dtype=float)
    windows_used = 0

    for segment in segments:
        for start in range(0, len(segment) - nperseg + 1, step):
            spectrum = np.fft.fft(segment[start : start + nperseg] * window)
            power_sum += np.abs(spectrum) ** 2 / scale
            windows_used += 1

    if windows_used == 0:
        raise ValueError("no complete Welch windows were available")

    psd = np.fft.fftshift(power_sum / windows_used)
    frequencies = np.fft.fftshift(
        np.fft.fftfreq(nperseg, d=1.0 / sampling_rate)
    )
    return frequencies, psd, nperseg, windows_used


def local_peaks(
    frequencies: np.ndarray,
    psd_db: np.ndarray,
    *,
    dc_guard_hz: float,
    top_n: int,
) -> list[int]:
    candidates = []
    for index in range(1, len(psd_db) - 1):
        if abs(frequencies[index]) <= dc_guard_hz:
            continue
        if psd_db[index] > psd_db[index - 1] and psd_db[index] > psd_db[index + 1]:
            candidates.append(index)
    candidates.sort(key=lambda index: psd_db[index], reverse=True)
    return candidates[:top_n]


def alias_frequency(frequency_hz: float, sampling_rate: float) -> float:
    folded = (frequency_hz + sampling_rate / 2.0) % sampling_rate - sampling_rate / 2.0
    return abs(folded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_file")
    parser.add_argument("--sampling-rate", type=positive_float, default=None)
    parser.add_argument("--target-frequency", type=positive_float, default=2000.0)
    parser.add_argument("--target-band-hz", type=positive_float, default=100.0)
    parser.add_argument("--nperseg", type=positive_int, default=4096)
    parser.add_argument("--overlap-percent", type=float, default=50.0)
    parser.add_argument("--center", choices=("midpoint", "mean", "none"), default="mean")
    parser.add_argument("--dc-guard-hz", type=positive_float, default=50.0)
    parser.add_argument("--top-n", type=positive_int, default=10)
    parser.add_argument("--out", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Analyze legacy/failed captures despite missing host-validation metadata.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not (0 <= args.overlap_percent < 100):
        print("Error: --overlap-percent must be in [0, 100).", file=sys.stderr)
        return 2

    try:
        capture = load_raw_csv(args.capture_file)
        sampling_rate = resolve_sampling_rate(capture, args.sampling_rate)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    host_validated = bool(
        capture.metadata
        and capture.metadata.get("host_transport_validation_passed", False)
    )
    if not host_validated and not args.allow_unvalidated:
        print(
            "Error: capture lacks passing host-validation metadata. "
            "Use --allow-unvalidated only for exploratory legacy analysis.",
            file=sys.stderr,
        )
        return 2

    try:
        frequencies, psd, nperseg, windows_used = welch_complex(
            centered_segments(capture, args.center),
            sampling_rate=sampling_rate,
            requested_nperseg=args.nperseg,
            overlap_percent=args.overlap_percent,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    psd_db = 10.0 * np.log10(np.maximum(psd, np.finfo(float).tiny))
    peaks = local_peaks(
        frequencies,
        psd_db,
        dc_guard_hz=args.dc_guard_hz,
        top_n=args.top_n,
    )

    print(
        f"Loaded {len(capture.i)} samples in {len(capture.segment_ids())} continuous "
        f"segment(s), fs={sampling_rate:g} Hz."
    )
    print(
        f"Welch PSD: nperseg={nperseg}, overlap={args.overlap_percent:g}%, "
        f"averaged windows={windows_used}, resolution={sampling_rate / nperseg:.3f} Hz."
    )
    if not host_validated:
        print("Warning: exploratory result from an unvalidated capture.")

    print(f"\nTop {len(peaks)} local PSD peaks outside +/-{args.dc_guard_hz:g} Hz:")
    print(f"{'Frequency (Hz)':>15}  {'PSD (dB/Hz)':>13}")
    for index in peaks:
        print(f"{frequencies[index]:15.3f}  {psd_db[index]:13.2f}")

    nyquist = sampling_rate / 2.0
    target_representable = args.target_frequency <= nyquist
    prominence_db: float | None = None
    if target_representable:
        target_mask = (
            np.abs(np.abs(frequencies) - args.target_frequency) <= args.target_band_hz
        )
        floor_mask = (
            (np.abs(frequencies) > args.dc_guard_hz)
            & ~target_mask
        )
        if target_mask.any() and floor_mask.any():
            target_peak = float(np.max(psd_db[target_mask]))
            floor = float(np.median(psd_db[floor_mask]))
            prominence_db = target_peak - floor
            print(
                f"\nTarget +/-{args.target_frequency:g} Hz: peak={target_peak:.2f} dB/Hz, "
                f"global median floor={floor:.2f} dB/Hz, heuristic prominence="
                f"{prominence_db:+.2f} dB."
            )
            if args.target_frequency == nyquist:
                print(
                    "Note: target is exactly at Nyquist; positive/negative signs "
                    "are indistinguishable."
                )
            print("This prominence is descriptive, not a calibrated detection probability.")
    else:
        folded = alias_frequency(args.target_frequency, sampling_rate)
        print(
            f"\nTarget {args.target_frequency:g} Hz is above Nyquist ({nyquist:g} Hz) "
            f"and aliases to {folded:g} Hz. Its original frequency cannot be identified "
            "from this sampled capture."
        )

    output_path = (
        Path(args.out)
        if args.out
        else capture.path.with_name(capture.path.stem + "_interference_psd.png")
    )
    if output_path.exists() and not args.force:
        print(f"Error: refusing to overwrite {output_path}; use --force.", file=sys.stderr)
        return 2

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=150)
    ax.plot(frequencies, psd_db, linewidth=0.8)
    ax.axvline(0, color="gray", linestyle=":", linewidth=1, label="DC")
    if target_representable:
        ax.axvline(args.target_frequency, color="crimson", linestyle="--", linewidth=1)
        ax.axvline(
            -args.target_frequency,
            color="crimson",
            linestyle="--",
            linewidth=1,
            label=f"+/-{args.target_frequency:g} Hz target",
        )
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("PSD (dB/Hz)")
    ax.set_title(
        f"Complex I/Q Welch PSD: {capture.path.name}\n"
        f"fs={sampling_rate:g} Hz, nperseg={nperseg}, windows={windows_used}"
    )
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper right")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
