#!/usr/bin/env python3
"""
spectrogram_view.py -- presentation-quality rendering of one or more
spectrogram capture files (same 256-value-per-line format as
visualizer.py/splitter.py expect).

Unlike visualizer.py (which writes a literal grayscale PNG matching the
raw pixel grid -- useful for a quick sanity check, but tiny and low
contrast), this script is meant for actually LOOKING at a capture: it
auto-scales colors to the data, upsizes the plot, labels the axes, marks
the DC bin, and traces the strongest non-DC ("motion") peak per column
so a real gesture sweep is visible at a glance.

Usage:
    # One file -> single detailed plot
    python3 spectrogram_view.py captures/swipe_left_raw.txt

    # Multiple files -> side-by-side grid (e.g. several splitter.py windows)
    python3 spectrogram_view.py captures/swipe_left/swipe_left1 captures/swipe_left/swipe_left2 ...

    # All windows in a folder at once
    python3 spectrogram_view.py captures/swipe_left/swipe_left*

Options:
    --dc-guard N     bins to mask on each side of center when finding the
                      motion peak (default: 5, matches STFT_check.py)
    --out PATH        output PNG path (default: derived from input name(s))
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLUMN_SIZE = 256


def load_columns(path):
    """Read a capture file into a (freq_bins x time) array, applying the
    same incomplete-first/last-line trimming as visualizer.py/splitter.py
    so results stay consistent across all three tools."""
    with open(path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        raise ValueError(f"{path}: file is empty")

    if len(lines[0].split()) < COLUMN_SIZE:
        lines = lines[1:]
    if lines and len(lines[-1].split()) < COLUMN_SIZE:
        lines = lines[:-1]
    if not lines:
        raise ValueError(f"{path}: no complete {COLUMN_SIZE}-value lines remain")

    columns = []
    for i, line in enumerate(lines, start=1):
        values = [int(x) for x in line.split()]
        if len(values) != COLUMN_SIZE:
            raise ValueError(
                f"{path}: usable line {i} contains {len(values)} values "
                f"instead of {COLUMN_SIZE}"
            )
        columns.append(values)

    return np.array(columns, dtype=np.int32).T  # freq_bins x time


def find_motion_peak(col, center, guard):
    masked = col.astype(float).copy()
    masked[max(0, center - guard):center + guard + 1] = -1
    return int(masked.argmax())


def plot_one(ax, arr, title, dc_guard, show_legend=False):
    n_bins, n_time = arr.shape
    center = n_bins // 2
    peaks = [find_motion_peak(arr[:, t], center, dc_guard) for t in range(n_time)]

    im = ax.imshow(arr, aspect="auto", origin="lower", cmap="inferno",
                    interpolation="bilinear")
    ax.axhline(center, color="cyan", linewidth=1, linestyle="--", alpha=0.6,
               label="DC (no motion)")
    ax.plot(range(n_time), peaks, color="lime", linewidth=1.6,
            marker="o", markersize=2.5, label="Motion peak (DC-masked)")
    ax.set_title(title, fontsize=11)
    if show_legend:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    return im


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", help="one or more capture files")
    p.add_argument("--dc-guard", type=int, default=5,
                   help="bins masked around center when finding the motion peak (default: 5)")
    p.add_argument("--out", default=None, help="output PNG path")
    args = p.parse_args()

    paths = [Path(f) for f in args.files]

    loaded = []
    for path in paths:
        try:
            loaded.append((path, load_columns(path)))
        except ValueError as e:
            print(f"Skipping {path}: {e}")

    if not loaded:
        print("No valid files to plot.")
        sys.exit(1)

    if len(loaded) == 1:
        path, arr = loaded[0]
        fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
        im = plot_one(ax, arr, f"Radar Spectrogram -- {path.name}", args.dc_guard,
                       show_legend=True)
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("log2 magnitude (spectrogram value)", fontsize=10)
        ax.set_xlabel("Time (STFT hop / column index)", fontsize=11)
        ax.set_ylabel("Frequency bin (Doppler) -- center = DC / stationary", fontsize=11)
        out_path = Path(args.out) if args.out else path.with_name(path.stem + "_view.png")
    else:
        n = len(loaded)
        fig, axes = plt.subplots(1, n, figsize=(3 * n, 5.5), dpi=150, squeeze=False)
        axes = axes[0]
        for ax, (path, arr) in zip(axes, loaded):
            plot_one(ax, arr, path.name, args.dc_guard)
            ax.set_xlabel("time", fontsize=8)
            ax.set_yticks([])
        axes[0].set_ylabel("freq bin (center=DC)", fontsize=9)
        fig.suptitle(f"{n} capture windows", fontsize=13, fontweight="bold")
        default_name = f"{paths[0].parent.name or 'captures'}_grid_view.png"
        out_path = Path(args.out) if args.out else Path(default_name)

    fig.tight_layout()
    fig.savefig(out_path)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()