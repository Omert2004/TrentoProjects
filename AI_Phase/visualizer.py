#!/usr/bin/env python3

import sys
from pathlib import Path

import numpy as np
from PIL import Image


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <input_file>")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    with open(input_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        print("Input file is empty.")
        sys.exit(1)

    # Skip incomplete first line
    first_count = len(lines[0].split())
    if first_count < 256:
        print(f"Ignoring incomplete first line ({first_count} values).")
        lines = lines[1:]

    if not lines:
        print("No valid lines remain.")
        sys.exit(1)

    # Skip incomplete last line
    last_count = len(lines[-1].split())
    if last_count < 256:
        print(f"Ignoring incomplete last line ({last_count} values).")
        lines = lines[:-1]

    if not lines:
        print("No valid lines remain.")
        sys.exit(1)

    columns = []

    for i, line in enumerate(lines, start=1):
        values = [int(x) for x in line.split()]

        if len(values) != 256:
            print(
                f"Error: usable line {i} contains "
                f"{len(values)} values instead of 256."
            )
            sys.exit(1)

        columns.append(values)

    width = len(columns)

    if width == 0:
        print("No complete columns found.")
        sys.exit(1)

    raw = np.array(columns, dtype=np.int32).T

    # Min-max normalize across the WHOLE capture (not per-column) so bin
    # brightness stays comparable across time -- a fixed multiplier like the
    # old *16 either clips real data (loses the difference between a strong
    # DC return and a strong motion peak) or wastes dynamic range if the
    # capture's values happen to be small. Scaling to the file's actual
    # min/max keeps every distinct value distinguishable with no clipping.
    lo, hi = raw.min(), raw.max()
    if hi == lo:
        arr = np.zeros_like(raw, dtype=np.uint8)
    else:
        arr = ((raw - lo) * (255.0 / (hi - lo))).astype(np.uint8)

    img = Image.fromarray(arr, mode="L")

    # Target directory inside STFT_Phase
    output_dir = input_path.parent / "output_images"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / input_path.with_suffix(".png").name
    img.save(output_path)

    print(f"Saved {output_path} ({width}x256)")


if __name__ == "__main__":
    main()