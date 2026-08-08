"""
rate_check.py -- verifies the FR5994 firmware is streaming clean,
stable ~4 kHz IFI/IFQ frames. No plotting, no FFT -- just counts and timing.
"""

import argparse
import struct
import time
import serial

SYNC1, SYNC2 = 0xAA, 0x55
FRAME_BODY_SIZE = 4

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=5.0, help="Seconds to measure")
    return p.parse_args()

def read_frame(ser):
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
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud. Measuring for {args.duration}s...")

    count = 0
    resyncs = 0
    first_ifi = last_ifi = None
    start = time.time()

    while time.time() - start < args.duration:
        frame = read_frame(ser)
        if frame is None:
            resyncs += 1
            continue
        ifi, ifq = frame
        if first_ifi is None:
            first_ifi = (ifi, ifq)
        last_ifi = (ifi, ifq)
        count += 1

    elapsed = time.time() - start
    ser.close()

    print(f"\nFrames received: {count}")
    print(f"Elapsed: {elapsed:.3f}s")
    print(f"Effective rate: {count / elapsed:.1f} Hz  (target: 4000 Hz)")
    print(f"Resync events (lost frame alignment): {resyncs}")
    print(f"First frame: {first_ifi}   Last frame: {last_ifi}")

if __name__ == "__main__":
    main()