"""
window_q15_generator.py -- one-off generator for window_q15.c/.h.

Builds a 256-point Hanning window and scales it to Q15 fixed-point
(-1.0..~1.0 represented as -32768..32767), then prints it as a C array
literal ready to paste into window_q15.c. Not part of the runtime
pipeline -- run this once whenever FFT_SIZE changes, and paste the output
into window_q15.c by hand.
"""

import numpy as np

fft_size = 256
# Hanning formula: 0.5 * (1 - cos(2*pi*n/N))
hanning_window = np.hanning(fft_size)
window_q15 = np.round(32767 * hanning_window).astype(int)

# Print as a C array literal
print(f"const int16_t window_q15[{fft_size}] = {{")
for i in range(0, fft_size, 8):
    row = ", ".join(f"{val:6d}" for val in window_q15[i:i + 8])
    print(f"    {row},")
print("};")

# Sanity checks
print(f"\nCheck 1: does it start at 0? -> {window_q15[0]}")
print(f"Check 2: are the midpoints near the peak (32767)? -> {window_q15[127]}, {window_q15[128]}")
print(f"Check 3: is it symmetric? (element 10 vs element 245) -> {window_q15[10]} == {window_q15[255 - 10]}")