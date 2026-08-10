"""
test1_adc_rate.py -- Test 1 of the UART throughput isolation plan.

Listens for the 5-byte 0xC2 rate-snapshot frames the firmware sends once a
second: [0xAA][0x55][0xC2][count_lo][count_hi], where count is the number
of ADC samples ACCEPTED into the ring buffer in the previous 1-second
window (see adc_sample_count in main.c / radar_configuration.c).

This is independent of DMA/UART transmit timing: Timer_A1 runs off ACLK,
completely separate from the Timer_A2/SMCLK timer that triggers the ADC.
So whatever number shows up here reflects ONLY the ADC-fill side of the
pipeline -- if it reads close to SAMPLING_RATE_HZ, the ADC is healthy and
the ~250 ms/column bottleneck lives downstream (shift-and-append / STFT
compute / DMA-UART path). If it reads much lower, the bottleneck is
upstream of all of that.

0xC0 spectrogram-column frames (259 bytes) are still arriving on the same
stream and are skipped over here, not parsed.

Usage:
    python3 test1_adc_rate.py --port COM7 --baud 115200 --duration 15
"""

import argparse
import struct
import time
import serial

SYNC1, SYNC2 = 0xAA, 0x55
SPECTROGRAM_MARKER = 0xC0
RATE_MARKER = 0xC2
PROFILE_MARKER = 0xC3   # 9-byte body; firmware sends this once/sec too, alongside 0xC2 --
                         # BUG FIX: this script used to not recognize it at all and would
                         # miscount every 0xC3 frame as a desync (once per second, every run).
COLUMN_SIZE = 256
SAMPLING_RATE_HZ = 4000   # must match radar_configuration.h, used only for the target line


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=15.0, help="Seconds to run (0 = until Ctrl+C)")
    return p.parse_args()


def read_frame(ser):
    """Returns ('rate', count) / ('column', None) / ('profile', None), or
    None on a genuine desync/timeout."""
    b = ser.read(1)
    if not b or b[0] != SYNC1:
        return None
    b = ser.read(1)
    if not b or b[0] != SYNC2:
        return None
    b = ser.read(1)
    if not b:
        return None
    marker = b[0]

    if marker == RATE_MARKER:
        body = ser.read(2)
        if len(body) != 2:
            return None
        (count,) = struct.unpack("<H", body)
        return ("rate", count)
    elif marker == SPECTROGRAM_MARKER:
        body = ser.read(COLUMN_SIZE)
        if len(body) != COLUMN_SIZE:
            return None
        return ("column", None)
    elif marker == PROFILE_MARKER:
        body = ser.read(6)  # hop_count, stft_ticks, dma_wait_ticks: 3x uint16
        if len(body) != 6:
            return None
        return ("profile", None)
    else:
        # Unknown marker -- treat as desync rather than guessing a length
        return None


def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud.")
    print(f"Watching for 1 Hz ADC rate snapshots (target: ~{SAMPLING_RATE_HZ} samples/sec).")
    print("Press Ctrl+C to stop.\n")

    resyncs = 0
    columns_seen = 0
    snapshots = []
    start = time.time()

    try:
        while args.duration == 0 or (time.time() - start) < args.duration:
            frame = read_frame(ser)
            if frame is None:
                resyncs += 1
                continue
            kind, value = frame
            if kind == "rate":
                snapshots.append(value)
                pct = 100.0 * value / SAMPLING_RATE_HZ
                print(f"[{time.time()-start:6.2f}s] ADC samples accepted in last 1s: "
                      f"{value:5d}  ({pct:5.1f}% of target {SAMPLING_RATE_HZ})")
            elif kind == "column":
                columns_seen += 1
            # "profile" (0xC3) frames are just consumed to keep byte
            # alignment -- see STFT_check.py if you want to inspect
            # STFT/DMA timing instead of ADC fill rate.
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    print(f"\nSnapshots received: {len(snapshots)}   Spectrogram columns seen (skipped): {columns_seen}")
    print(f"Resync events: {resyncs}")
    if snapshots:
        avg = sum(snapshots) / len(snapshots)
        print(f"Average ADC rate: {avg:.1f} samples/sec  "
              f"({100.0*avg/SAMPLING_RATE_HZ:.1f}% of target {SAMPLING_RATE_HZ})")
        print(f"Min: {min(snapshots)}   Max: {max(snapshots)}")


if __name__ == "__main__":
    main()