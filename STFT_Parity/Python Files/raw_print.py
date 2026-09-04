"""Compatibility notice for the retired AI_Phase raw-frame tool."""

import sys


def main() -> int:
    print(
        "raw_print.py is intentionally retired for AI_Phase: the active firmware "
        "streams CRC-protected on-chip STFT columns, not raw I/Q. Use "
        "STFT_check.py for live text output, radar_stft_capture.py for a live "
        "plot, or the Raw_Data_Capture project for raw I/Q.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

