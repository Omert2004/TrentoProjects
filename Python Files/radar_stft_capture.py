"""
radar_stft_capture.py

Replicates the STFT.c pipeline from the thesis project (sliding-window,
Hanning-windowed, complex FFT over I+jQ, log-magnitude, frequency-shifted)
on the PC side, fed from the raw IFI/IFQ samples streamed out of the
MSP430FR5994 firmware over serial (COM7 by default).

The current firmware (radar_configuration.c / main.c) just streams raw
"IFI,IFQ\r\n" ADC pairs -- it does *not* do any windowing/FFT on-chip, unlike
the older MSP432 thesis firmware (STFT.c). This script reproduces that
missing STFT stage on the PC:

    1. Samples are pushed into an FFT_SIZE-long sliding window (I and Q
       separately), advancing FFT_HOP samples at a time -- exactly like the
       shift-and-append loop in main.c.
    2. Each window is centered (subtract half full-scale) and multiplied by
       a Hanning window (matches window.c, minus the ADC-specific /8192
       scaling that file baked into the window values).
    3. A complex FFT is taken over (I + jQ) -- matches arm_cfft_f32 being
       called on interleaved I/Q data in STFT.c.
    4. Magnitude-squared -> log2 -> clip negative to 0 -> fftshift, exactly
       mirroring STFT_compute_next_segment().
    5. Each resulting row is appended to a growing spectrogram matrix
       (rows = time segments, columns = frequency bins), plotted live as a
       waterfall, and saved to disk on exit.

Usage:
    python radar_stft_capture.py
    python radar_stft_capture.py --port COM7 --fft-size 256 --fft-hop 128

Dependencies:
    pip install pyserial numpy matplotlib scipy
"""

import argparse
import sys

import numpy as np
import serial
import matplotlib.pyplot as plt
from matplotlib.widgets import Button
from scipy.io import savemat

import struct

SYNC1, SYNC2 = 0xAA, 0x55
FRAME_BODY_SIZE = 4  # IFI (u16 LE) + IFQ (u16 LE)


def parse_args():
    p = argparse.ArgumentParser(description="Reproduce the thesis STFT.c pipeline on data streamed from a COM port.")
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--fft-size", type=int, default=256, help="FFT_SIZE (default 256, matches STFT.h)")
    p.add_argument("--fft-hop", type=int, default=128, help="FFT_HOP (default 128, matches STFT.h)")
    p.add_argument("--adc-bits", type=int, default=12,
                   help="ADC resolution of the incoming samples (default 12, for ADC12_B on the FR5994; "
                        "the original MSP432 thesis used a 14-bit ADC14 result and centered on 8192)")
    p.add_argument("--max-segments-shown", type=int, default=150, help="Rows kept on screen in the live plot")
    p.add_argument("--segments", type=int, default=0, help="Stop after this many segments (0 = run until Ctrl+C)")
    p.add_argument("--outfile", default="radar_spectrogram")
    return p.parse_args()


class SerialManager:
    """Manages the serial connection state for the UI."""
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None
        self.is_connected = False

    def toggle_connection(self, event, btn):
        if self.is_connected:
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.is_connected = False
            btn.label.set_text('Connect')
            print(f"\nDisconnected from {self.port}.")
        else:
            try:
                self.ser = serial.Serial(self.port, self.baud, timeout=1)
                self.ser.reset_input_buffer()
                self.is_connected = True
                btn.label.set_text('Disconnect')
                print(f"\nConnected to {self.port} @ {self.baud} baud.")
            except serial.SerialException as e:
                print(f"\nCould not open {self.port}: {e}")
        plt.gcf().canvas.draw_idle()



def read_sample(ser):
    """Reads one binary frame: [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi]."""
    b = ser.read(1)
    if not b or b[0] != SYNC1:
        return None
    b = ser.read(1)
    if not b or b[0] != SYNC2:
        return None
    body = ser.read(FRAME_BODY_SIZE)
    if len(body) != FRAME_BODY_SIZE:
        return None
    ifi, ifq = struct.unpack("<HH", body)
    return ifi, ifq

def compute_segment(i_win, q_win, window, half_scale):
    """Mirrors STFT_compute_next_segment() in STFT.c."""
    fi = i_win.astype(np.float64) - half_scale
    fq = q_win.astype(np.float64) - half_scale
    windowed = (fi * window) + 1j * (fq * window)

    spectrum = np.fft.fft(windowed)                    # arm_cfft_f32(&fft, tbuf, 0, 1)
    magnitude = spectrum.real ** 2 + spectrum.imag ** 2  # magnitude = r*r + i*i  (no sqrt, as in STFT.c)

    with np.errstate(divide="ignore"):
        log_mag = np.log2(magnitude)
    log_mag = np.where(magnitude <= 0, 0.0, log_mag)
    log_mag[log_mag < 0] = 0.0                          # if(spectrogram[...][...] < 0) ... = 0

    return np.fft.fftshift(log_mag)                     # (n + FFT_SIZE/2) % FFT_SIZE


def save_spectrogram(segments, outfile):
    if not segments:
        print("No segments captured, nothing to save.")
        return
    spec = np.array(segments, dtype=np.float64)  # rows = time segments, cols = frequency bins
    np.savetxt(f"{outfile}.csv", spec, delimiter=",")
    savemat(f"{outfile}.mat", {"spectrogram": spec})
    print(f"\nSaved {spec.shape[0]} segments x {spec.shape[1]} frequency bins to:")
    print(f"  {outfile}.csv")
    print(f"  {outfile}.mat   (MATLAB variable: spectrogram)")


def main():
    args = parse_args()
    
    # Initialize the serial connection state (Starts Disconnected)
    ser_manager = SerialManager(args.port, args.baud)

    fft_size = args.fft_size
    fft_hop = args.fft_hop
    half_scale = 2 ** (args.adc_bits - 1)  # centers ADC codes around 0 (8192 for the old 14-bit thesis ADC)

    window = np.hanning(fft_size)  # same shape as window.c (that file also baked in an extra /8192 ADC-scale factor)

    i_buf = np.zeros(fft_size)
    q_buf = np.zeros(fft_size)
    segments = []

    max_rows = args.max_segments_shown
    waterfall = np.zeros((fft_size, max_rows))
    
    # Setup Plot
    plt.ion()
    fig, ax = plt.subplots()
    plt.subplots_adjust(bottom=0.2) # Make space at the bottom for the UI button
    
    img = ax.imshow(waterfall, aspect="auto", cmap="viridis", origin="lower",
                 extent=[0, max_rows, 0, fft_size])
    ax.set_ylabel("Frequency bin (fftshift'ed, DC in the middle)")
    ax.set_xlabel("Time segment (most recent on the right)")
    ax.set_title(f"Live STFT spectrogram  (FFT_SIZE={fft_size}, FFT_HOP={fft_hop})")
    cbar = fig.colorbar(img, ax=ax)
    cbar.set_label("log2(|I + jQ|^2)")

    # Add Connect / Disconnect button to the bottom right
    ax_btn = plt.axes([0.75, 0.05, 0.15, 0.075])
    initial_btn_text = 'Disconnect' if ser_manager.is_connected else 'Connect'
    btn = Button(ax_btn, initial_btn_text)
    btn.on_clicked(lambda event: ser_manager.toggle_connection(event, btn))

    print(f"Started in DISCONNECTED mode. Click 'Connect' on the plot window to listen on {args.port} @ {args.baud} baud.")
    print(f"FFT_SIZE={fft_size}, FFT_HOP={fft_hop}, ADC centered on {half_scale} ({args.adc_bits}-bit).")
    print("Press Ctrl+C in the terminal to stop and save.")

    hop_i, hop_q = [], []
    segment_count = 0

    try:
        while True:
            # If connected, read data
            if ser_manager.is_connected and ser_manager.ser and ser_manager.ser.is_open:
                try:
                    sample = read_sample(ser_manager.ser)
                except serial.SerialException:
                    print("\nConnection lost.")
                    ser_manager.toggle_connection(None, btn)
                    sample = None

                if sample is None:
                    # No data yet, pause briefly to keep UI responsive
                    plt.pause(0.01)
                    continue

                ifi, ifq = sample
                hop_i.append(ifi)
                hop_q.append(ifq)

                if len(hop_i) == fft_hop:
                    # shift the window left by FFT_HOP and append the new hop --
                    # same as the STFT_input_I/Q shift-and-append loop in main.c
                    i_buf = np.concatenate([i_buf[fft_hop:], hop_i])
                    q_buf = np.concatenate([q_buf[fft_hop:], hop_q])
                    hop_i, hop_q = [], []

                    segment = compute_segment(i_buf, q_buf, window, half_scale)
                    segments.append(segment)
                    segment_count += 1

                    waterfall = np.roll(waterfall, -1, axis=1)
                    waterfall[:, -1] = segment
                    img.set_data(waterfall)
                    img.set_clim(waterfall.min(), waterfall.max())
                    fig.canvas.draw_idle()
                    plt.pause(0.001)

                    if args.segments and segment_count >= args.segments:
                        print(f"\nCaptured requested {args.segments} segments.")
                        break
            else:
                # Disconnected state: just keep the UI alive while waiting for the user to click 'Connect'
                plt.pause(0.1)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if ser_manager.ser and ser_manager.ser.is_open:
            ser_manager.ser.close()
        save_spectrogram(segments, args.outfile)
        plt.ioff()
        print("Close the plot window to exit.")
        plt.show()

if __name__ == "__main__":
    main()