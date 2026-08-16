"""Create a raw I/Q sanity-check plot from a validated capture CSV."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from raw_data import load_raw_csv, resolve_sampling_rate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("--sampling-rate", type=float, default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-points", type=int, default=20000)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_points <= 0:
        print("Error: --max-points must be positive.", file=sys.stderr)
        return 2
    try:
        capture = load_raw_csv(args.input_file)
        sampling_rate = resolve_sampling_rate(capture, args.sampling_rate)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    output_path = (
        Path(args.out)
        if args.out
        else capture.path.with_name(capture.path.stem + "_raw_view.png")
    )
    if output_path.exists() and not args.force:
        print(f"Error: refusing to overwrite {output_path}; use --force.", file=sys.stderr)
        return 2

    count = len(capture.i)
    step = max(1, int(np.ceil(count / args.max_points)))
    selected = slice(None, None, step)
    time_s = np.arange(count, dtype=float) / sampling_rate

    fig, (ax_signal, ax_iq) = plt.subplots(2, 1, figsize=(12, 8), dpi=150)
    ax_signal.plot(time_s[selected], capture.i[selected], linewidth=0.7, label="I")
    ax_signal.plot(time_s[selected], capture.q[selected], linewidth=0.7, label="Q", alpha=0.8)
    ax_signal.axhline(2048, color="black", linestyle=":", linewidth=1, label="ADC midpoint")
    ax_signal.set_ylabel("ADC code")
    ax_signal.set_xlabel("Time (s)")
    ax_signal.set_ylim(-50, 4145)
    ax_signal.grid(True, alpha=0.2)
    ax_signal.legend(loc="upper right")

    i_centered = capture.i[selected] - 2048
    q_centered = capture.q[selected] - 2048
    ax_iq.scatter(i_centered, q_centered, s=3, alpha=0.25, rasterized=True)
    ax_iq.axhline(0, color="black", linewidth=0.7, alpha=0.5)
    ax_iq.axvline(0, color="black", linewidth=0.7, alpha=0.5)
    ax_iq.set_xlabel("I - 2048 (ADC codes)")
    ax_iq.set_ylabel("Q - 2048 (ADC codes)")
    ax_iq.set_title("I/Q constellation (display-decimated if necessary)")
    ax_iq.grid(True, alpha=0.2)
    ax_iq.set_aspect("equal", adjustable="datalim")

    fig.suptitle(
        f"Raw radar capture: {capture.path.name}\n"
        f"{count} samples at {sampling_rate:g} Hz ({count / sampling_rate:.3f} s)"
    )
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
