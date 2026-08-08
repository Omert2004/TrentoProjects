"""
raw_print.py -- prints every IFI/IFQ frame received, raw, one per line.
Useful for eyeballing whether data looks sane and watching it live.
"""

import argparse
import struct
import serial

SYNC1, SYNC2 = 0xAA, 0x55
FRAME_BODY_SIZE = 4

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--raw-bytes", action="store_true",
                    help="Also print the raw hex bytes of each frame")
    p.add_argument("--limit", type=int, default=0,
                    help="Stop after this many frames (0 = run until Ctrl+C)")
    return p.parse_args()

def read_frame_verbose(ser, show_raw):
    b = ser.read(1)
    if not b:
        print("[timeout: no byte received in 1s]")
        return None
    if b[0] != SYNC1:
        print(f"[resync: expected 0xAA, got 0x{b[0]:02X}]")
        return None

    b = ser.read(1)
    if not b:
        print("[timeout waiting for second sync byte]")
        return None
    if b[0] != SYNC2:
        print(f"[resync: expected 0x55 after 0xAA, got 0x{b[0]:02X}]")
        return None

    body = ser.read(FRAME_BODY_SIZE)
    if len(body) != FRAME_BODY_SIZE:
        print(f"[timeout: only got {len(body)}/{FRAME_BODY_SIZE} body bytes]")
        return None

    ifi, ifq = struct.unpack("<HH", body)
    if show_raw:
        raw_hex = " ".join(f"{x:02X}" for x in (SYNC1, SYNC2, *body))
        print(f"IFI={ifi:5d}  IFQ={ifq:5d}   [{raw_hex}]")
    else:
        print(f"IFI={ifi:5d}  IFQ={ifq:5d}")
    return ifi, ifq

def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud. Press Ctrl+C to stop.\n")

    count = 0
    try:
        while True:
            frame = read_frame_verbose(ser, args.raw_bytes)
            if frame is not None:
                count += 1
                if args.limit and count >= args.limit:
                    print(f"\nReached limit of {args.limit} frames.")
                    break
    except KeyboardInterrupt:
        print(f"\nStopped. Total frames printed: {count}")
    finally:
        ser.close()

if __name__ == "__main__":
    main()