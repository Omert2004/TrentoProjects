"""
profile_check.py -- reads the multiplexed 0xC0 / 0xC2 / 0xC3 stream and
prints the per-hop timing breakdown: how much of each ~250-330 ms column
period is spent inside STFT_compute_next_segment() (LEA FFT + the
unaccelerated log2f() magnitude loop) vs waiting on the DMA/UART path.

Frame formats:
  0xC0  spectrogram column   (259 bytes total)            -- skipped here
  0xC2  ADC rate snapshot    (5 bytes total)               -- printed
  0xC3  profiling snapshot   (9 bytes total)                -- printed:
        [hop_count u16][stft_ticks u16][dma_wait_ticks u16], all little-endian,
        ticks are Timer_A1/ACLK units (32768 Hz -> ms = ticks / 32.768)

Usage:
    python3 profile_check.py --port COM7 --baud 115200 --duration 15
"""

import argparse
import struct
import time
import serial

SYNC1, SYNC2 = 0xAA, 0x55
COL_MARKER, RATE_MARKER, PROFILE_MARKER = 0xC0, 0xC2, 0xC3
COLUMN_SIZE = 256
ACLK_HZ = 32768.0
SAMPLING_RATE_HZ = 4000  # must match radar_configuration.h, for the rate target line


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=15.0, help="Seconds to run (0 = until Ctrl+C)")
    return p.parse_args()


def read_frame(ser):
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

    if marker == COL_MARKER:
        body = ser.read(COLUMN_SIZE)
        if len(body) != COLUMN_SIZE:
            return None
        return ("column", None)
    elif marker == RATE_MARKER:
        body = ser.read(2)
        if len(body) != 2:
            return None
        (count,) = struct.unpack("<H", body)
        return ("rate", count)
    elif marker == PROFILE_MARKER:
        body = ser.read(6)
        if len(body) != 6:
            return None
        hop_count, stft_ticks, dma_ticks = struct.unpack("<HHH", body)
        return ("profile", (hop_count, stft_ticks, dma_ticks))
    else:
        return None  # unknown marker -- desync


def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud.")
    print("Watching for 1 Hz profiling snapshots (STFT compute vs DMA wait).")
    print("Press Ctrl+C to stop.\n")

    resyncs = 0
    columns_seen = 0
    start = time.time()

    try:
        while args.duration == 0 or (time.time() - start) < args.duration:
            frame = read_frame(ser)
            if frame is None:
                resyncs += 1
                continue
            kind, value = frame
            t = time.time() - start

            if kind == "rate":
                pct = 100.0 * value / SAMPLING_RATE_HZ
                print(f"[{t:6.2f}s] RATE    accepted={value:5d} samples/s ({pct:5.1f}% of {SAMPLING_RATE_HZ})")

            elif kind == "profile":
                hop_count, stft_ticks, dma_ticks = value
                stft_ms = stft_ticks / ACLK_HZ * 1000.0
                dma_ms = dma_ticks / ACLK_HZ * 1000.0
                per_hop_stft_ms = stft_ms / hop_count if hop_count else 0.0
                per_hop_dma_ms = dma_ms / hop_count if hop_count else 0.0
                total_per_hop = per_hop_stft_ms + per_hop_dma_ms
                print(f"[{t:6.2f}s] PROFILE hops={hop_count:3d}  "
                      f"STFT compute: {stft_ms:7.1f} ms total ({per_hop_stft_ms:6.2f} ms/hop)  "
                      f"DMA wait: {dma_ms:7.1f} ms total ({per_hop_dma_ms:6.2f} ms/hop)  "
                      f"| accounted/hop: {total_per_hop:6.2f} ms")

            else:
                columns_seen += 1

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    print(f"\nColumns seen (skipped): {columns_seen}   Resyncs: {resyncs}")


if __name__ == "__main__":
    main()