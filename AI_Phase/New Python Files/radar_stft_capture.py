"""Live viewer for validated AI_Phase D0 column packets.

This tool never writes dataset files. Use radar_dataset_capture.py for captures
with validation metadata and hashes.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque

import numpy as np

from stft_protocol import ColumnPacket, StftFrameReader


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--columns", type=int, default=150)
    parser.add_argument("--dc-guard", type=int, default=5)
    parser.add_argument("--display-mask-dc", action="store_true")
    args = parser.parse_args()
    try:
        import serial
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation
    except ModuleNotFoundError as exc:
        print(f"Error: missing dependency {exc.name}; install -r requirements.txt", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0)
    port.reset_input_buffer()
    reader = StftFrameReader(port)
    history: deque[tuple[int, ...]] = deque(maxlen=max(1, args.columns))

    figure, axis = plt.subplots(figsize=(11, 6))
    matrix = np.zeros((256, max(1, args.columns)), dtype=np.uint8)
    image = axis.imshow(matrix, origin="lower", aspect="auto", cmap="inferno", interpolation="nearest", vmin=0, vmax=31)
    axis.axhline(128, color="cyan", linestyle="--", linewidth=0.8)
    axis.set_xlabel("Recent STFT columns")
    axis.set_ylabel("fftshift frequency bin")
    axis.set_title("AI_Phase live on-chip STFT (CRC validated)")
    figure.colorbar(image, ax=axis, label="floor(log2 magnitude)")

    def update(_):
        for _attempt in range(64):
            frame = reader.read_frame()
            if frame is None:
                break
            if isinstance(frame, ColumnPacket):
                history.append(frame.values)
        if history:
            data = np.asarray(history, dtype=np.uint8).T
            matrix.fill(0)
            matrix[:, -data.shape[1]:] = data
            shown = matrix.copy()
            if args.display_mask_dc:
                center = shown.shape[0] // 2
                shown[max(0, center-args.dc_guard):center+args.dc_guard+1, :] = 0
            image.set_data(shown)
        return (image,)

    animation = FuncAnimation(figure, update, interval=50, cache_frame_data=False, blit=False)
    _ = animation
    try:
        plt.show()
    finally:
        port.close()

    stats = reader.stats
    print(f"Parser statistics: {stats.to_dict()}")
    if stats.corruption_detected or stats.first_reported_drop_count not in (None, 0):
        print("Stream integrity: FAIL", file=sys.stderr)
        return 2
    print("Stream integrity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

