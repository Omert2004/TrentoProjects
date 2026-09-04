"""Render a validated AI_Phase capture as a literal 256-by-N grayscale PNG."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

from stft_data import load_stft_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file")
    parser.add_argument("--allow-unvalidated", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        capture = load_stft_capture(args.input_file, require_validated=not args.allow_unvalidated)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # Fixed 0..31 scaling preserves amplitude comparability across sessions.
    pixels = np.rint(capture.columns.T.astype(np.float64) * (255.0 / 31.0)).astype(np.uint8)
    output = Path(args.out) if args.out else capture.path.with_name(capture.path.stem + "_raw.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="L").save(output)
    print(f"Saved {output} ({capture.columns.shape[0]}x256 columns/bins)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

