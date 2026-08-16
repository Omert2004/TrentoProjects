#!/usr/bin/env python3
"""
interference_check.py -- checks for the BGT60 "multiplicative interference
at 2 kHz" artifact Enrico's thesis describes (section 4.2.1), on THIS
board/wiring specifically.

Why a single long FFT and not the 256-point windowed STFT: the STFT trades
frequency resolution for time resolution (you need to see gestures change
over time), which is exactly the wrong tool for this question. Here we
don't care about time at all -- we want the highest-resolution possible
view of the noise floor across the whole capture, so a single FFT over
every sample in the file is the right tool. (This mirrors how the thesis
itself frames the artifact -- as a property of the raw sampled signal,
before STFT segmentation ever enters the picture.)

Expects the CSV format written by raw_capture.py:
    sample_idx,segment,I,Q
    0,0,2051,2049
    1,0,2050,2048
    ...

Usage:
    # Capture a few seconds of TRUE no-movement data first (empty room,
    # sensor pointed at nothing moving) with raw_capture.py, then:
    python3 interference_check.py captures/no_movement_20250101_120000.csv

    # Try a different sampling rate hypothesis, or disable centering/window:
    python3 interference_check.py captures/foo.csv --sampling-rate 4000
    python3 interference_check.py captures/foo.csv --no-window

Output:
    A PNG plot of magnitude (dB) vs frequency, spanning -fs/2..+fs/2, with
    dashed lines marking +/-2000 Hz (the thesis's reported artifact
    location) so it's visually obvious whether a peak lands there or not.
    Also prints the top N peaks by magnitude (DC-guarded) as text, so you
    have numbers to cite to Sinan, not just a picture.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ADC_MIDPOINT = 2048   # 12-bit ADC code centering, matches firmware's STFT.c
                       # ((int16_t)stft_input_I[i] - 2048) convention


def load_iq(path):
    """Reads raw_capture.py's CSV format. Returns (I, Q) as float arrays,
    ignoring the segment column (interference characterization doesn't
    care about session-chaining boundaries -- if you captured multiple
    segments back-to-back, treating them as one continuous signal here is
    fine; any join discontinuity would itself show up as broadband noise,
    not a clean 2 kHz line, so it won't be mistaken for the artifact)."""
    I, Q = [], []
    with open(path, "r") as f:
        header = f.readline()  # discard header line
        if not header.strip().lower().startswith("sample_idx"):
            print(f"Warning: unexpected header line: {header.strip()!r} "
                  f"-- expected raw_capture.py's CSV format.")
        for line_no, line in enumerate(f, start=2):
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 4:
                print(f"Skipping malformed line {line_no}: {line!r}")
                continue
            _idx, _seg, i_val, q_val = parts
            I.append(float(i_val))
            Q.append(float(q_val))

    if not I:
        raise ValueError(f"{path}: no usable I/Q rows found")

    return np.array(I), np.array(Q)


def find_peaks_excluding_dc(freqs, magnitude_db, dc_guard_hz, top_n):
    """Simple local-max peak picking, masking out the DC-adjacent band so
    the (expected, uninteresting) strong DC/clutter return doesn't drown
    out everything else in the top-N list. Same DC-guard idea already
    used elsewhere in this project (STFT_check.py's find_motion_peak)."""
    mask = np.abs(freqs) > dc_guard_hz
    candidate_idx = np.where(mask)[0]

    # local maxima among candidates: value greater than both neighbors
    peaks = []
    for idx in candidate_idx:
        if idx == 0 or idx == len(magnitude_db) - 1:
            continue
        if magnitude_db[idx] > magnitude_db[idx - 1] and magnitude_db[idx] > magnitude_db[idx + 1]:
            peaks.append(idx)

    peaks.sort(key=lambda idx: magnitude_db[idx], reverse=True)
    return peaks[:top_n]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("capture_file", help="CSV file from raw_capture.py")
    p.add_argument("--sampling-rate", type=float, default=4000.0,
                    help="Hz, must match SAMPLING_RATE_HZ used during capture (default: 4000)")
    p.add_argument("--no-window", action="store_true",
                    help="Skip Hanning windowing before the FFT (raw rectangular window). "
                         "Windowing reduces spectral leakage/smearing from the capture's "
                         "start/end edges -- leave it on unless you have a specific reason not to.")
    p.add_argument("--no-center", action="store_true",
                    help="Skip ADC-midpoint (2048) centering -- use this if your capture "
                         "wasn't taken with a 12-bit ADC, or if you want to center on the "
                         "capture's own mean instead (see --center-on-mean).")
    p.add_argument("--center-on-mean", action="store_true",
                    help="Center I/Q on their own sample mean instead of the fixed ADC "
                         "midpoint (2048). Useful if there's a DC offset/bias in the analog "
                         "front-end that differs from the nominal ADC midpoint.")
    p.add_argument("--dc-guard-hz", type=float, default=50.0,
                    help="Bandwidth around 0 Hz excluded from peak search (default: 50 Hz) "
                         "-- the static/clutter DC return is expected and not interesting here.")
    p.add_argument("--top-n", type=int, default=10,
                    help="How many top peaks (by magnitude, DC-guarded) to print (default: 10)")
    p.add_argument("--out", default=None, help="output PNG path")
    args = p.parse_args()

    path = Path(args.capture_file)
    I, Q = load_iq(path)
    n = len(I)
    duration_s = n / args.sampling_rate
    print(f"Loaded {n} I/Q samples from {path} ({duration_s:.2f}s @ {args.sampling_rate:.0f} Hz)")

    if duration_s < 1.0:
        print("Warning: capture is under 1 second -- frequency resolution will be coarse "
              "(resolution = sampling_rate / N samples). For a clean read on a 2 kHz "
              "artifact, a few seconds is much better than a fraction of one.")

    # --- Centering (mirrors STFT.c / thesis Algorithm 3's centering step) ---
    if args.no_center:
        I_c, Q_c = I, Q
    elif args.center_on_mean:
        I_c = I - I.mean()
        Q_c = Q - Q.mean()
        print(f"Centered on sample mean: I_mean={I.mean():.1f}  Q_mean={Q.mean():.1f}")
    else:
        I_c = I - ADC_MIDPOINT
        Q_c = Q - ADC_MIDPOINT

    # --- Complex signal: I as real, Q as imaginary -- same convention as
    # STFT.c / the thesis's Algorithm 3, which is what lets the FFT tell
    # approach from recede (positive vs negative frequency) instead of
    # just magnitude of motion. ---
    complex_signal = I_c + 1j * Q_c

    if not args.no_window:
        window = np.hanning(n)
        complex_signal = complex_signal * window

    # --- Single FFT over the WHOLE capture (not the 256-point windowed
    # STFT) -- see module docstring for why. ---
    spectrum = np.fft.fftshift(np.fft.fft(complex_signal))
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / args.sampling_rate))
    magnitude_db = 20 * np.log10(np.abs(spectrum) + 1e-9)

    # --- Peak search, DC-guarded ---
    peak_indices = find_peaks_excluding_dc(freqs, magnitude_db, args.dc_guard_hz, args.top_n)

    print(f"\nTop {len(peak_indices)} peaks (DC-guarded at +/-{args.dc_guard_hz:.0f} Hz):")
    print(f"{'Freq (Hz)':>12}  {'Magnitude (dB)':>15}")
    for idx in peak_indices:
        print(f"{freqs[idx]:12.1f}  {magnitude_db[idx]:15.2f}")

    # Specifically check near +/-2000 Hz (thesis's reported artifact location)
    near_2k_mask = np.abs(np.abs(freqs) - 2000.0) < 100.0
    if near_2k_mask.any():
        near_2k_peak_db = magnitude_db[near_2k_mask].max()
        overall_floor_db = np.median(magnitude_db[np.abs(freqs) > args.dc_guard_hz])
        delta = near_2k_peak_db - overall_floor_db
        print(f"\nNear +/-2000 Hz (+/-100 Hz window): peak = {near_2k_peak_db:.2f} dB, "
              f"vs. median noise floor = {overall_floor_db:.2f} dB "
              f"(delta = {delta:+.2f} dB)")
        if delta > 10:
            print("-> Clear elevated peak near 2 kHz: consistent with the thesis's reported "
                  "artifact being present on this board too.")
        elif delta > 3:
            print("-> Mild elevation near 2 kHz: inconclusive, worth a longer/cleaner capture "
                  "before drawing a conclusion either way.")
        else:
            print("-> No meaningful elevation near 2 kHz: does NOT look like the same artifact "
                  "is present on this board/wiring.")
    else:
        print(f"\n(Sampling rate {args.sampling_rate:.0f} Hz means the +/-2000 Hz check window "
              f"falls outside the representable range -- can't check this artifact location "
              f"at this rate.)")

    # --- Plot ---
    fig, ax = plt.subplots(figsize=(11, 6), dpi=150)
    ax.plot(freqs, magnitude_db, linewidth=0.8, color="steelblue")
    ax.axvline(2000, color="crimson", linestyle="--", linewidth=1, alpha=0.7,
               label="+2000 Hz (thesis artifact location)")
    ax.axvline(-2000, color="crimson", linestyle="--", linewidth=1, alpha=0.7)
    ax.axvline(0, color="gray", linestyle=":", linewidth=1, alpha=0.5, label="DC")
    ax.set_xlabel("Frequency (Hz)", fontsize=11)
    ax.set_ylabel("Magnitude (dB)", fontsize=11)
    ax.set_title(f"Noise-floor FFT -- {path.name}\n"
                 f"({n} samples, {duration_s:.2f}s @ {args.sampling_rate:.0f} Hz, "
                 f"resolution {args.sampling_rate/n:.3f} Hz/bin)", fontsize=11)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()

    out_path = Path(args.out) if args.out else path.with_name(path.stem + "_interference_check.png")
    fig.savefig(out_path)
    print(f"\nSaved plot: {out_path}")


if __name__ == "__main__":
    main()