"""Interactive multi-take capture for the 2 kHz packetized raw firmware.

Unlike the obsolete version, this script does not send ``S`` and does not
expect ``D2``/``D3`` packets. Every take uses the same CRC/sequence-validated
packet parser and metadata contract as ``raw_serial_capture.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from raw_serial_capture import capture_stream, nonnegative_float, positive_float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=positive_float, required=True)
    parser.add_argument("--default-duration", type=positive_float, default=2.0)
    parser.add_argument("--out", default="captures")
    parser.add_argument(
        "--rate-tolerance-percent",
        type=nonnegative_float,
        default=2.0,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud <= 0:
        print("Error: --baud must be positive.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
        return 2

    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    any_failed = False
    print(
        f"Connected to {args.port} @ {args.baud} baud. "
        f"Configured sampling rate: {args.sampling_rate:g} Hz."
    )
    print("The firmware streams continuously; this tool never sends a start command.\n")

    try:
        while True:
            label = input("Label for next take (blank to quit): ").strip()
            if not label:
                break

            duration_text = input(
                f"  Duration in seconds [default {args.default_duration:g}]: "
            ).strip()
            try:
                duration = (
                    positive_float(duration_text)
                    if duration_text
                    else args.default_duration
                )
            except argparse.ArgumentTypeError as exc:
                print(f"  Invalid duration: {exc}")
                continue

            input("  Press Enter when the scene/gesture is ready...")
            metadata = capture_stream(
                serial_port,
                port_name=args.port,
                baud=args.baud,
                sampling_rate_hz=args.sampling_rate,
                duration_s=duration,
                label=label,
                out_dir=Path(args.out),
                rate_tolerance_percent=args.rate_tolerance_percent,
                reset_input=True,
            )

            status = "PASS" if metadata["host_transport_validation_passed"] else "FAIL"
            print(
                f"  {metadata['received_samples']} samples, "
                f"{metadata['observed_receive_rate_hz']:.1f} samples/s, "
                f"capture validation={status}"
            )
            print(f"  CSV: {metadata['csv_path']}")
            print(f"  Metadata: {metadata['metadata_path']}\n")
            any_failed |= not metadata["host_transport_validation_passed"]
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        serial_port.close()

    return 2 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
