"""Display CRC-validated CNN predictions emitted by AI_Phase firmware."""

from __future__ import annotations

import argparse
from datetime import datetime
import sys
import time

from stft_protocol import CnnResultPacket, StftFrameReader

LABELS = (
    "Left horizontal scroll",
    "Right horizontal scroll",
    "Clicking hand",
    "Empty",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument(
        "--show-logits",
        action="store_true",
        help="append the four raw integer scores for debugging",
    )
    args = parser.parse_args()

    if args.baud <= 0 or args.duration <= 0:
        print("Error: baud and duration must be positive.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    port.reset_input_buffer()
    reader = StftFrameReader(port)
    deadline = time.perf_counter() + args.duration
    print(f"Connected to {args.port}. Waiting for CNN results...")
    try:
        while time.perf_counter() < deadline:
            frame = reader.read_frame()
            if isinstance(frame, CnnResultPacket):
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                message = (
                    f"{timestamp} | Detection #{frame.inference_sequence} | "
                    "Detected action: "
                    f"{LABELS[frame.predicted_class]}"
                )
                if args.show_logits:
                    message += f" | logits={frame.logits}"
                print(message, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
    print(
        f"Results received: {reader.stats.cnn_result_frames_accepted} | "
        f"CRC errors: {reader.stats.crc_errors} | "
        f"resyncs: {reader.stats.resync_events}"
    )
    passed = (
        reader.stats.cnn_result_frames_accepted > 0
        and not reader.stats.corruption_detected
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
