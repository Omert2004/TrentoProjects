"""
STFT_check.py -- prints incoming frames with frame counting and timestamps.
Handles both frame types:
  - raw IFI/IFQ frames:        [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi]
  - spectrogram column frames: [0xAA][0x55][0xC0][256 bytes of int8_t column data]
"""

import argparse
import struct
import time
import serial

SYNC1, SYNC2 = 0xAA, 0x55
SPECTROGRAM_MARKER = 0xC0
RATE_MARKER = 0xC2      # Test 1: 1 Hz ADC-rate snapshot, 2-byte body
PROFILE_MARKER = 0xC3   # STFT-compute vs DMA-wait profiling, 6-byte body
IFRAME_BODY_SIZE = 4      # IFI (u16 LE) + IFQ (u16 LE)
COLUMN_SIZE = 256         # FFT_SIZE
DC_GUARD = 5              # bins excluded on each side of center when finding the motion peak


def find_motion_peak(column):
    """Peak away from the static DC/clutter return at center. The DC bin
    almost always dominates (strong static reflection even with no motion),
    so a flat max() just reports clutter every time. Masking a small band
    around center reveals the actual Doppler-shifted (motion) energy."""
    center = len(column) // 2
    candidates = [(b, v) for b, v in enumerate(column) if abs(b - center) > DC_GUARD]
    return max(candidates, key=lambda x: x[1])

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--raw-bytes", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--show-columns", action="store_true")
    p.add_argument("--log-file", default=None,
                    help="If set, append each spectrogram column as a space-separated "
                         "line to this file (same format splitter.py/visualizer.py expect)")
    return p.parse_args()

def read_frame_verbose(ser, show_raw, show_columns, frame_idx, elapsed_time, log_fh=None):
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

    # Third byte tells us which frame type this is
    b = ser.read(1)
    if not b:
        print("[timeout waiting for frame type byte]")
        return None
    frame_type = b[0]

    # Timestamp string prefix for output
    time_prefix = f"[Frame {frame_idx:5d} | {elapsed_time:6.2f}s]"

    if frame_type == SPECTROGRAM_MARKER:
        body = ser.read(COLUMN_SIZE)
        if len(body) != COLUMN_SIZE:
            print(f"[timeout: only got {len(body)}/{COLUMN_SIZE} column bytes]")
            return None
        column = struct.unpack(f"<{COLUMN_SIZE}b", body)
        peak_bin = max(range(COLUMN_SIZE), key=lambda i: column[i])
        peak_val = column[peak_bin]
        motion_bin, motion_val = find_motion_peak(column)

        print(f"{time_prefix} [SPECTROGRAM] dc_peak_bin={peak_bin:3d}  dc_peak_val={peak_val:3d}  "
              f"| motion_bin={motion_bin:3d}  motion_val={motion_val:3d}")
        
        if show_columns:
            print("  " + " ".join(f"{v:3d}" for v in column))

        if log_fh:
            log_fh.write(" ".join(str(v) for v in column) + "\n")
            log_fh.flush()   # flush per-column so Ctrl+C never loses buffered data

        return ("column", column)

    elif frame_type == RATE_MARKER:
        # Test 1 rate-snapshot frame: 2 body bytes. Not otherwise used by
        # this script -- just consumed so byte alignment isn't lost. Use
        # profile_check.py to actually see these values.
        body = ser.read(2)
        if len(body) != 2:
            print("[timeout: only got partial 0xC2 rate-frame body]")
            return None
        return ("rate", None)

    elif frame_type == PROFILE_MARKER:
        # Profiling frame: 6 body bytes (hop_count, stft_ticks, dma_wait_ticks).
        # Same idea -- consumed here just to stay aligned, see profile_check.py.
        body = ser.read(6)
        if len(body) != 6:
            print("[timeout: only got partial 0xC3 profile-frame body]")
            return None
        return ("profile", None)

    else:
        # Treat frame_type byte as low byte of IFI
        rest = ser.read(IFRAME_BODY_SIZE - 1)
        if len(rest) != IFRAME_BODY_SIZE - 1:
            print(f"[timeout: only got {len(rest)}/{IFRAME_BODY_SIZE-1} remaining body bytes]")
            return None
        body = bytes([frame_type]) + rest
        ifi, ifq = struct.unpack("<HH", body)
        if show_raw:
            raw_hex = " ".join(f"{x:02X}" for x in (SYNC1, SYNC2, *body))
            print(f"{time_prefix} IFI={ifi:5d}  IFQ={ifq:5d}   [{raw_hex}]")
        else:
            print(f"{time_prefix} IFI={ifi:5d}  IFQ={ifq:5d}")
        return ("raw", (ifi, ifq))

def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud. Press Ctrl+C to stop.\n")

    log_fh = open(args.log_file, "a") if args.log_file else None
    if log_fh:
        print(f"Logging spectrogram columns to {args.log_file}\n")

    count = 0
    start_time = time.time()
    last_sec_print = 0

    try:
        while True:
            current_time = time.time()
            elapsed = current_time - start_time
            
            # Print a distinct 2-second marker every full 2 seconds passed
            current_interval = int(elapsed) // 2 * 2
            if current_interval > last_sec_print:
                print(f"\n--- [ TIME: {current_interval:3d}.0s | Total Frames Captured: {count} ] ---")
                last_sec_print = current_interval

            # Pass (count + 1) as the current frame index
            frame = read_frame_verbose(ser, args.raw_bytes, args.show_columns, count + 1, elapsed, log_fh)
            
            if frame is not None:
                count += 1
                if args.limit and count >= args.limit:
                    print(f"\nReached limit of {args.limit} frames.")
                    break
    except KeyboardInterrupt:
        print(f"\nStopped. Total frames printed: {count}")
    finally:
        ser.close()
        if log_fh:
            log_fh.close()

if __name__ == "__main__":
    main()