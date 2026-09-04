"""Create a labeled plot from a validated AI_Phase STFT capture."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

from stft_data import load_stft_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("--out")
    parser.add_argument("--allow-unvalidated", action="store_true")
    parser.add_argument("--dc-guard", type=int, default=5)
    parser.add_argument("--trace-motion-peak", action="store_true")
    args = parser.parse_args()
    try:
        capture = load_stft_capture(args.input_file, require_validated=not args.allow_unvalidated)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except (ValueError, ModuleNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    metadata = capture.metadata or {}
    sampling_rate = float(metadata.get("configured_sampling_rate_hz", 4000.0))
    fft_size = int(metadata.get("fft_size", 256))
    hop = int(metadata.get("fft_hop", 128))
    frequency = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / sampling_rate))
    times = np.arange(capture.columns.shape[0]) * hop / sampling_rate
    matrix = capture.columns.T

    figure, axis = plt.subplots(figsize=(11, 6.5), dpi=150)
    extent = [0.0, times[-1] if len(times) > 1 else hop / sampling_rate, frequency[0], frequency[-1]]
    image = axis.imshow(matrix, origin="lower", aspect="auto", interpolation="nearest", cmap="inferno", vmin=0, vmax=31, extent=extent)
    axis.axhline(0.0, color="cyan", linestyle="--", linewidth=0.8, label="DC")
    if args.trace_motion_peak:
        masked = matrix.copy().astype(np.int16)
        center = fft_size // 2
        masked[max(0, center-args.dc_guard):center+args.dc_guard+1, :] = -1
        peaks = np.argmax(masked, axis=0)
        axis.plot(times, frequency[peaks], color="lime", linewidth=0.8, label="strongest non-DC bin")
        axis.legend(loc="upper right")
    axis.set_xlabel("Time (s)")
    axis.set_ylabel("Doppler frequency (Hz)")
    axis.set_title(capture.path.name)
    figure.colorbar(image, ax=axis, label="floor(log2 magnitude)")
    figure.tight_layout()
    output = Path(args.out) if args.out else capture.path.with_name(capture.path.stem + "_view.png")
    figure.savefig(output)
    print(f"Saved {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

