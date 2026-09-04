"""Print validated AI_Phase STFT frames and integrity statistics."""

from __future__ import annotations

import argparse
import sys
import time

from stft_protocol import ColumnPacket, ProfilePacket, RatePacket, StftFrameReader


def motion_peak(values: tuple[int, ...], guard: int) -> tuple[int, int]:
    center = len(values) // 2
    candidates = ((index, value) for index, value in enumerate(values) if abs(index - center) > guard)
    return max(candidates, key=lambda item: item[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--limit", type=int, default=0, help="column limit; 0 runs until Ctrl+C")
    parser.add_argument("--every", type=int, default=1)
    parser.add_argument("--dc-guard", type=int, default=5)
    parser.add_argument("--show-columns", action="store_true")
    args = parser.parse_args()
    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    port.reset_input_buffer()
    reader = StftFrameReader(port)
    start = time.perf_counter()
    columns = 0
    print(f"Connected to {args.port} @ {args.baud}. Press Ctrl+C to stop.")
    try:
        while not args.limit or columns < args.limit:
            frame = reader.read_frame()
            if isinstance(frame, ColumnPacket):
                columns += 1
                if columns % max(1, args.every) == 0:
                    peak_bin = max(range(len(frame.values)), key=frame.values.__getitem__)
                    move_bin, move_value = motion_peak(frame.values, args.dc_guard)
                    print(
                        f"[{time.perf_counter()-start:7.2f}s] column={columns:6d} "
                        f"seq={frame.column_sequence:5d} sample={frame.first_new_sample_index:10d} "
                        f"drops={frame.cumulative_drop_count} peak={peak_bin}:{frame.values[peak_bin]} "
                        f"motion={move_bin}:{move_value}"
                    )
                    if args.show_columns:
                        print(" ".join(str(value) for value in frame.values))
            elif isinstance(frame, RatePacket):
                print(f"[RATE] report={frame.report_sequence}, accepted={frame.accepted_samples}, drops={frame.cumulative_drop_count}")
            elif isinstance(frame, ProfilePacket):
                print(f"[PROFILE] report={frame.report_sequence}, hops={frame.hop_count}, stft_ticks={frame.stft_ticks}, dma_ticks={frame.dma_wait_ticks}")
    except KeyboardInterrupt:
        pass
    finally:
        port.close()

    stats = reader.stats
    passed = columns > 0 and not stats.corruption_detected and stats.first_reported_drop_count in (None, 0)
    print(f"\nColumns: {columns}")
    print(f"Parser statistics: {stats.to_dict()}")
    print(f"Stream integrity: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
