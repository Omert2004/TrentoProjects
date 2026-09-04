"""Capture and compare one exact MSP430 STFT pipeline execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import numpy as np

from parity_protocol import CaptureAssembler, ParityFrameReader


def load_window(path: Path, count: int) -> np.ndarray:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"window_q15\s*\[[^]]+\]\s*=\s*\{(.*?)\};", text, re.S)
    if not match:
        raise ValueError(f"could not find window_q15 array in {path}")
    values = [int(value) for value in re.findall(r"-?\d+", match.group(1))]
    if len(values) != count:
        raise ValueError(f"{path} contains {len(values)} coefficients, expected {count}")
    return np.asarray(values, dtype=np.int16)


def int16_wrap(values: np.ndarray) -> np.ndarray:
    return ((values.astype(np.int64) + 32768) % 65536 - 32768).astype(np.int16)


def q15_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return int16_wrap((a.astype(np.int64) * b.astype(np.int64)) >> 15)


def bit_reverse_indices(length: int) -> np.ndarray:
    bits = length.bit_length() - 1
    return np.asarray([
        int(f"{index:0{bits}b}"[::-1], 2) for index in range(length)
    ], dtype=np.int32)


def q15_twiddle(index: int, length: int) -> tuple[int, int]:
    angle = -2.0 * np.pi * index / length
    real = int(np.rint(np.cos(angle) * 32768.0))
    imag = int(np.rint(np.sin(angle) * 32768.0))
    return max(-32768, min(32767, real)), max(-32768, min(32767, imag))


def fixed_q15_fft_candidate(interleaved: np.ndarray) -> np.ndarray:
    """Radix-2 fixed-scaled candidate; board stage data is authoritative.

    TI documents FFT(x)/N scaling but does not fully specify every LEA
    intermediate rounding detail. The transmitted post-FFT buffer tells us
    whether this candidate is bit exact on the installed DSPLib/LEA revision.
    """

    complex_values = interleaved.reshape(-1, 2).astype(np.int64)
    length = complex_values.shape[0]
    data = complex_values[bit_reverse_indices(length)].copy()
    size = 2
    while size <= length:
        half = size // 2
        for start in range(0, length, size):
            for offset in range(half):
                wr, wi = q15_twiddle(offset * length // size, length)
                br, bi = data[start + offset + half]
                tr = (br * wr - bi * wi) >> 15
                ti = (br * wi + bi * wr) >> 15
                ar, ai = data[start + offset]
                data[start + offset] = ((ar + tr) >> 1, (ai + ti) >> 1)
                data[start + offset + half] = ((ar - tr) >> 1, (ai - ti) >> 1)
        data = int16_wrap(data).astype(np.int64)
        size *= 2
    return data.astype(np.int16).reshape(-1)


def column_from_fft(interleaved: np.ndarray) -> tuple[np.ndarray, int]:
    values = interleaved.reshape(-1, 2).astype(np.int64)
    magnitude = values[:, 0] * values[:, 0] + values[:, 1] * values[:, 1]
    overflow_count = int(np.count_nonzero(magnitude > np.iinfo(np.int32).max))
    logs = np.asarray([int(value).bit_length() - 1 if value > 0 else 0 for value in magnitude], dtype=np.uint8)
    return np.fft.fftshift(logs), overflow_count


def comparison(expected: np.ndarray, actual: np.ndarray) -> dict:
    delta = actual.astype(np.int64) - expected.astype(np.int64)
    mismatch = np.flatnonzero(delta)
    return {
        "exact": not bool(mismatch.size),
        "matching_values": int(expected.size - mismatch.size),
        "total_values": int(expected.size),
        "mismatch_count": int(mismatch.size),
        "max_absolute_difference": int(np.max(np.abs(delta))) if delta.size else 0,
        "first_mismatches": [
            {
                "index": int(index),
                "board": int(expected[index]),
                "python": int(actual[index]),
                "difference": int(delta[index]),
            }
            for index in mismatch[:20]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--window-file", default=None)
    parser.add_argument("--out-dir", default="parity_captures")
    args = parser.parse_args()
    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    default_window = Path(__file__).resolve().parent.parent / "src" / "window_q15.c"
    window_path = Path(args.window_file) if args.window_file else default_window
    try:
        window = load_window(window_path, 256)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    port.reset_input_buffer()
    reader = ParityFrameReader(port)
    assembler = CaptureAssembler()
    capture = None
    deadline = time.perf_counter() + args.timeout
    print("Waiting for one complete E0/E1/E1/E2 parity group...")
    try:
        while time.perf_counter() < deadline and capture is None:
            frame = reader.read_frame()
            if frame is not None:
                capture = assembler.add(frame)
    finally:
        port.close()
    if capture is None:
        print(f"Error: no complete group in {args.timeout:g}s; parser={reader.stats}", file=sys.stderr)
        return 2

    raw = capture.raw
    known_flags = 0xF1
    clutter_enabled = bool(raw.flags & 1)
    diff_shift = (raw.flags >> 4) & 0x0F
    if raw.fft_size != 256 or raw.fft_hop != 128 or raw.flags & ~known_flags:
        print(f"Error: unexpected firmware configuration: {raw}", file=sys.stderr)
        return 2
    if not clutter_enabled or diff_shift != 4 or raw.i.size != raw.fft_size + 1:
        print(
            "Error: parity firmware must send 257 raw samples with "
            "clutter cancellation enabled and DIFF_SHIFT=4.",
            file=sys.stderr,
        )
        return 2
    if raw.cumulative_drop_count != 0:
        print(f"Error: MCU reported {raw.cumulative_drop_count} capture drops.", file=sys.stderr)
        return 2

    input_i = np.diff(raw.i.astype(np.int64))
    input_q = np.diff(raw.q.astype(np.int64))
    centered_i = np.clip(input_i << diff_shift, -32768, 32767).astype(np.int16)
    centered_q = np.clip(input_q << diff_shift, -32768, 32767).astype(np.int16)
    py_windowed = np.empty(512, dtype=np.int16)
    py_windowed[0::2] = q15_multiply(centered_i, window)
    py_windowed[1::2] = q15_multiply(centered_q, window)
    py_fft = fixed_q15_fft_candidate(py_windowed)
    py_column, py_overflows = column_from_fft(py_fft)
    board_post_column, board_overflows = column_from_fft(capture.fft.values)

    # Floating reference uses the same quantized windowed input but performs an
    # ideal complex FFT/N before rounding to integer FFT components.
    z = py_windowed[0::2].astype(np.float64) + 1j * py_windowed[1::2].astype(np.float64)
    float_fft = np.fft.fft(z) / raw.fft_size
    float_interleaved = np.empty(512, dtype=np.int16)
    float_interleaved[0::2] = np.clip(np.rint(float_fft.real), -32768, 32767).astype(np.int16)
    float_interleaved[1::2] = np.clip(np.rint(float_fft.imag), -32768, 32767).astype(np.int16)
    float_column, _ = column_from_fft(float_interleaved)

    report = {
        "schema_version": 1,
        "capture_sequence": raw.sequence,
        "sampling_rate_hz": raw.sampling_rate_hz,
        "fft_size": raw.fft_size,
        "fft_hop": raw.fft_hop,
        "raw_sample_count": int(raw.i.size),
        "clutter_cancel_enabled": clutter_enabled,
        "diff_shift": diff_shift,
        "first_sample_index": raw.first_sample_index,
        "cumulative_drop_count": raw.cumulative_drop_count,
        "parser_statistics": vars(reader.stats),
        "window_stage": comparison(capture.windowed.values, py_windowed),
        "fft_stage_candidate": comparison(capture.fft.values, py_fft),
        "postprocess_from_board_fft": comparison(capture.column.values, board_post_column),
        "end_to_end_candidate": comparison(capture.column.values, py_column),
        "float_reference_column": comparison(capture.column.values, float_column),
        "board_fft_magnitude_int32_overflows": board_overflows,
        "python_fft_magnitude_int32_overflows": py_overflows,
    }
    report["bit_exact_parity_proven"] = bool(
        report["window_stage"]["exact"]
        and report["fft_stage_candidate"]["exact"]
        and report["postprocess_from_board_fft"]["exact"]
        and report["end_to_end_candidate"]["exact"]
        and not reader.stats.corruption_detected
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"parity_seq{raw.sequence:05d}"
    npz_path = out_dir / f"{stem}.npz"
    report_path = out_dir / f"{stem}.report.json"
    np.savez_compressed(
        npz_path,
        raw_i=raw.i,
        raw_q=raw.q,
        stft_input_difference_i=input_i.astype(np.int16),
        stft_input_difference_q=input_q.astype(np.int16),
        stft_input_q15_i=centered_i,
        stft_input_q15_q=centered_q,
        window_q15=window,
        board_windowed=capture.windowed.values,
        python_windowed=py_windowed,
        board_fft=capture.fft.values,
        python_fft_candidate=py_fft,
        board_column=capture.column.values,
        python_column_candidate=py_column,
        python_column_from_board_fft=board_post_column,
        float_reference_column=float_column,
    )
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for name in ("window_stage", "fft_stage_candidate", "postprocess_from_board_fft", "end_to_end_candidate", "float_reference_column"):
        item = report[name]
        print(f"{name:28s}: {'EXACT' if item['exact'] else 'DIFF'}; mismatches={item['mismatch_count']}, max_abs={item['max_absolute_difference']}")
    print(f"Parser statistics: {vars(reader.stats)}")
    print(f"Bit-exact parity: {'PASS' if report['bit_exact_parity_proven'] else 'NOT YET PROVEN'}")
    print(f"Arrays: {npz_path}")
    print(f"Report: {report_path}")
    return 0 if report["bit_exact_parity_proven"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
