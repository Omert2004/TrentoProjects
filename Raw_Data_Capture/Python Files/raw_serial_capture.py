"""
raw_serial_capture.py -- logs raw I/Q frames to CSV, for the CURRENT
continuous-streaming firmware (main.c / radar_configuration.c: streams
from boot, no host command, no D2/D3 markers).

Frame format (6 bytes, no marker byte -- same as raw_print.py/rate_check.py):
    [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi]

This is NOT the same protocol raw_capture.py expects (that script wants a
'S' command + 0xD2/0xD3-marked responses, which belongs to a different,
older firmware version). Use THIS script with the firmware in this repo.

Writes the same CSV shape interference_check.py already reads:
    sample_idx,segment,I,Q

Usage:
    # Capture for a fixed duration (recommended for the interference check --
    # get a few seconds so interference_check.py has decent frequency resolution):
    python raw_serial_capture.py --port COM7 --duration 5 --label no_movement --out captures/

    # Or capture until Ctrl+C:
    python raw_serial_capture.py --port COM7 --label no_movement --out captures/
"""

import argparse
import struct
import time
from pathlib import Path

import serial

SYNC1, SYNC2 = 0xAA, 0x55
FRAME_BODY_SIZE = 4   # IFI (u16 LE) + IFQ (u16 LE)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=None,
                    help="Seconds to capture (omit to run until Ctrl+C)")
    p.add_argument("--label", default="capture",
                    help="Used in the output filename, e.g. 'no_movement' (default: capture)")
    p.add_argument("--out", default="captures", help="Output directory")
    return p.parse_args()


def read_frame(ser):
    """Returns (ifi, ifq) or None on resync/timeout. Single-byte
    resync-on-failure, same approach as raw_print.py/rate_check.py."""
    b = ser.read(1)
    if not b or b[0] != SYNC1:
        return None
    b = ser.read(1)
    if not b or b[0] != SYNC2:
        return None
    body = ser.read(FRAME_BODY_SIZE)
    if len(body) != FRAME_BODY_SIZE:
        return None
    return struct.unpack("<HH", body)


def main():
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{args.label}_{timestamp}.csv"

    print(f"Connecting to {args.port} @ {args.baud} baud...")
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()

    if args.duration:
        print(f"Capturing for {args.duration}s. This firmware streams "
              f"continuously from boot -- no need to press anything, "
              f"just hold the scene still starting now.")
    else:
        print("Capturing until Ctrl+C. This firmware streams continuously "
              "from boot -- hold the scene still.")

    samples = 0
    resyncs = 0
    start = time.time()

    with open(out_path, "w") as f:
        f.write("sample_idx,segment,I,Q\n")
        try:
            while True:
                if args.duration and (time.time() - start) >= args.duration:
                    break

                frame = read_frame(ser)
                if frame is None:
                    resyncs += 1
                    continue

                ifi, ifq = frame
                f.write(f"{samples},0,{ifi},{ifq}\n")
                samples += 1

                if samples % 2000 == 0:
                    elapsed = time.time() - start
                    print(f"  {samples} samples ({elapsed:.1f}s, "
                          f"{resyncs} resyncs so far)")
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            ser.close()

    elapsed = time.time() - start
    print(f"\nCaptured {samples} samples in {elapsed:.2f}s "
          f"({samples / elapsed if elapsed > 0 else 0:.1f} samples/sec)")
    print(f"Resync events: {resyncs}")
    if resyncs > samples * 0.05 and samples > 0:
        print("Warning: resync count is high relative to samples captured -- "
              "check that this is really the continuous-streaming firmware "
              "and not something else on the wire.")
    print(f"Saved to: {out_path}")


if __name__ == "__main__":
    main()