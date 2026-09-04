"""Read CRC-protected ADC-rate and STFT profiling reports."""

from __future__ import annotations

import argparse
import sys
import time

from stft_protocol import ProfilePacket, RatePacket, StftFrameReader

ACLK_HZ = 32768.0
SAMPLING_RATE_HZ = 2000.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=15.0)
    args = parser.parse_args()
    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    port.reset_input_buffer()
    reader = StftFrameReader(port)
    profiles = 0
    start = time.perf_counter()
    print(f"Connected to {args.port} @ {args.baud} baud.")
    try:
        while time.perf_counter() - start < args.duration:
            frame = reader.read_frame()
            t = time.perf_counter() - start
            if isinstance(frame, RatePacket):
                pct = 100.0 * frame.accepted_samples / SAMPLING_RATE_HZ
                print(f"[{t:6.2f}s] RATE report={frame.report_sequence:5d}, accepted={frame.accepted_samples:5d} ({pct:5.1f}%), drops={frame.cumulative_drop_count}")
            elif isinstance(frame, ProfilePacket):
                profiles += 1
                stft_ms = frame.stft_ticks / ACLK_HZ * 1000.0
                dma_ms = frame.dma_wait_ticks / ACLK_HZ * 1000.0
                divisor = frame.hop_count or 1
                print(f"[{t:6.2f}s] PROFILE report={frame.report_sequence:5d}, hops={frame.hop_count:3d}; STFT={stft_ms:7.1f} ms ({stft_ms/divisor:6.2f}/hop); DMA wait={dma_ms:7.1f} ms ({dma_ms/divisor:6.2f}/hop)")
    except KeyboardInterrupt:
        pass
    finally:
        port.close()

    stats = reader.stats
    passed = profiles > 0 and not stats.corruption_detected and stats.first_reported_drop_count in (None, 0)
    print(f"\nProfile reports: {profiles}; validated columns skipped: {stats.columns_accepted}")
    print(f"Parser statistics: {stats.to_dict()}")
    print(f"Validation: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
