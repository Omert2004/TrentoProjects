"""Live value smoke test for the packetized raw I/Q stream.

Printing is intentionally not a timing or loss test. Use ``--every`` to reduce
terminal load while still parsing and validating every received packet.
"""

from __future__ import annotations

import argparse
import sys
import time

from raw_protocol import (
    MAX_SAMPLES_PER_PACKET,
    RawPacketReader,
    packet_size,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--raw-bytes", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Accepted samples; 0 = until Ctrl+C")
    parser.add_argument(
        "--every",
        type=int,
        default=1,
        help="Print every Nth accepted sample while validating all packets (default: 1).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud <= 0 or args.limit < 0 or args.every <= 0:
        print("Error: invalid baud, limit, or every value.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
        return 2

    serial_port = serial.Serial(args.port, args.baud, timeout=0.25)
    serial_port.reset_input_buffer()
    reader = RawPacketReader(
        serial_port,
        read_size=packet_size(MAX_SAMPLES_PER_PACKET),
    )
    started_ns = time.perf_counter_ns()

    print(
        f"Connected to {args.port} @ {args.baud} baud. "
        "This display is not a timing measurement."
    )
    accepted = 0
    printed = 0
    packets = 0
    try:
        while not args.limit or accepted < args.limit:
            packet = reader.read_packet()
            if packet is None:
                continue
            packets += 1
            if args.raw_bytes:
                print(
                    f"packet_seq={packet.packet_sequence} bytes: "
                    + " ".join(f"{byte:02X}" for byte in packet.raw)
                )

            for offset, (ifi, ifq) in enumerate(packet.samples):
                if args.limit and accepted >= args.limit:
                    break
                accepted += 1
                if (accepted - 1) % args.every:
                    continue
                printed += 1
                sample_index = (packet.first_sample_index + offset) & 0xFFFFFFFF
                print(
                    f"sample={sample_index:10d} packet={packet.packet_sequence:5d} "
                    f"IFI={ifi:4d} IFQ={ifq:4d} drops={packet.cumulative_drop_count}"
                )
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        elapsed_s = max((time.perf_counter_ns() - started_ns) / 1_000_000_000, 1e-12)
        serial_port.close()

    print(
        f"Accepted {accepted} samples in {packets} packets "
        f"({accepted / elapsed_s:.1f} samples/s); printed {printed}."
    )
    print(f"Parser statistics: {reader.stats.to_dict()}")
    if reader.stats.corruption_detected:
        print("FAIL: parser detected corruption/resynchronization.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
