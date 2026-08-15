"""
radar_stft_capture.py

Live spectrogram viewer + logger for the MSP430FR5994 firmware.

Firmware (STFT.c) computes the windowed FFT on-chip via LEA and streams
finished spectrogram columns as:

    [0xAA][0x55][0xC0][256 bytes of int8_t column data]

This script's job is just: read those columns, plot them as a rolling
waterfall, and optionally log them to a text file (same "256
space-separated values per line" format splitter.py / visualizer.py /
spectrogram_view.py already expect).

Rendering is decoupled from data arrival on purpose. Columns arrive at
~31 Hz; a naive "redraw after every column" loop can't keep up with that
plus a full-canvas imshow redraw, so the GUI event queue backs up and the
window appears to freeze. Instead: a background timer (FuncAnimation)
redraws at a fixed ~15 fps, and each tick drains *all* columns that have
piled up in the serial buffer since the last tick in one non-blocking
read, feeding them into the waterfall before a single redraw.

Usage:
    python radar_stft_capture.py --port COM7
    python radar_stft_capture.py --port COM7 --log-file capture1.txt

Dependencies:
    pip install pyserial numpy matplotlib
"""

import argparse
import struct

import numpy as np
import serial
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button

SYNC1, SYNC2 = 0xAA, 0x55
SPECTROGRAM_MARKER = 0xC0
RATE_MARKER = 0xC2        # 1 Hz ADC-rate snapshot, 2-byte body -- parsed & discarded here
PROFILE_MARKER = 0xC3     # STFT/DMA timing snapshot, 6-byte body -- parsed & discarded here
COLUMN_SIZE = 256         # FFT_SIZE
HEADER_SIZE = 3           # [0xAA][0x55][marker]
DC_GUARD = 5              # bins excluded on each side of center to suppress the static clutter peak


def parse_args():
    p = argparse.ArgumentParser(description="Live view of on-chip spectrogram columns streamed over serial.")
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--max-segments-shown", type=int, default=150, help="Columns kept on screen in the live plot")
    p.add_argument("--segments", type=int, default=0, help="Stop after this many columns (0 = run until Ctrl+C)")
    p.add_argument("--redraw-hz", type=float, default=15.0, help="Plot refresh rate (decoupled from data rate)")
    p.add_argument("--log-file", default=None,
                    help="If set, append each column as a space-separated line to this file "
                         "(same format splitter.py/visualizer.py/spectrogram_view.py expect)")
    return p.parse_args()


class FrameParser:
    """Byte-buffer based parser: feed it raw bytes as they arrive (any
    chunk size), pull out complete frames as they become available,
    silently resync on the next 0xAA 0x55 pair if anything looks corrupt.
    Doing this on a buffer (instead of blocking ser.read(1) calls per
    byte) is what lets the caller drain a whole burst of backlogged
    columns in one non-blocking pass."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf.extend(data)

    def pop_columns(self):
        """Returns a list of newly-completed spectrogram columns (np.ndarray,
        length COLUMN_SIZE). Other frame types (rate/profile) are consumed
        and discarded so byte alignment is preserved."""
        columns = []
        while True:
            idx = self.buf.find(bytes([SYNC1, SYNC2]))
            if idx == -1:
                # No sync pair found -- keep at most the last byte, in case
                # it's a lone 0xAA waiting for its 0x55.
                if len(self.buf) > 1:
                    del self.buf[:-1]
                break
            if idx > 0:
                del self.buf[:idx]  # drop garbage before the sync pair

            if len(self.buf) < HEADER_SIZE:
                break  # need the marker byte too

            frame_type = self.buf[2]
            if frame_type == SPECTROGRAM_MARKER:
                need = HEADER_SIZE + COLUMN_SIZE
            elif frame_type == RATE_MARKER:
                need = HEADER_SIZE + 2
            elif frame_type == PROFILE_MARKER:
                need = HEADER_SIZE + 6
            else:
                # Not a recognized marker -- drop just the leading 0xAA and
                # let the next loop iteration resync on whatever 0xAA 0x55
                # comes next, rather than guessing a frame length.
                del self.buf[0]
                continue

            if len(self.buf) < need:
                break  # wait for more bytes next feed()

            body = bytes(self.buf[HEADER_SIZE:need])
            del self.buf[:need]

            if frame_type == SPECTROGRAM_MARKER:
                col = np.array(struct.unpack(f"<{COLUMN_SIZE}b", body), dtype=np.int32)
                columns.append(col)
            # RATE_MARKER / PROFILE_MARKER: already consumed above, nothing to return

        return columns


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
                # timeout=0 -> non-blocking reads: read() / in_waiting-based
                # reads return immediately with whatever bytes are already
                # available instead of stalling the redraw timer.
                self.ser = serial.Serial(self.port, self.baud, timeout=0)
                self.ser.reset_input_buffer()
                self.is_connected = True
                btn.label.set_text('Disconnect')
                print(f"\nConnected to {self.port} @ {self.baud} baud.")
            except serial.SerialException as e:
                print(f"\nCould not open {self.port}: {e}")
        plt.gcf().canvas.draw_idle()


def main():
    args = parse_args()
    ser_manager = SerialManager(args.port, args.baud)
    parser = FrameParser()

    max_rows = args.max_segments_shown
    waterfall = np.zeros((COLUMN_SIZE, max_rows))

    log_fh = open(args.log_file, "a") if args.log_file else None
    if log_fh:
        print(f"Logging spectrogram columns to {args.log_file}")

    fig, ax = plt.subplots()
    plt.subplots_adjust(bottom=0.2)

    img = ax.imshow(waterfall, aspect="auto", cmap="inferno", origin="lower",
                     extent=[0, max_rows, 0, COLUMN_SIZE], vmin=0, vmax=30)
    ax.axhline(COLUMN_SIZE // 2, color="cyan", linewidth=1, linestyle="--",
               alpha=0.6, label="DC (no motion)")
    
    # Optional: Draw horizontal lines representing the boundary of the DC Guard
    center = COLUMN_SIZE // 2
    ax.axhline(center + DC_GUARD, color="white", linewidth=0.5, linestyle=":", alpha=0.4)
    ax.axhline(center - DC_GUARD, color="white", linewidth=0.5, linestyle=":", alpha=0.4, label="DC Guard Band")

    ax.legend(loc="upper right", fontsize=8)
    ax.set_ylabel("Frequency bin (Doppler) -- center = DC / stationary")
    ax.set_xlabel("Time (most recent column on the right)")
    ax.set_title("Live radar spectrogram (on-chip STFT)")
    cbar = fig.colorbar(img, ax=ax)
    cbar.set_label("log2 magnitude (spectrogram value)")

    ax_btn = plt.axes([0.75, 0.05, 0.15, 0.075])
    initial_btn_text = 'Disconnect' if ser_manager.is_connected else 'Connect'
    btn = Button(ax_btn, initial_btn_text)
    btn.on_clicked(lambda event: ser_manager.toggle_connection(event, btn))

    print(f"Started in DISCONNECTED mode. Click 'Connect' to listen on {args.port} @ {args.baud} baud.")
    print("Close the plot window (or Ctrl+C in the terminal) to stop.")

    state = {"segment_count": 0, "tick": 0, "stop": False}

    def on_tick(_frame):
        nonlocal waterfall
        if state["stop"]:
            return (img,)

        if not (ser_manager.is_connected and ser_manager.ser and ser_manager.ser.is_open):
            return (img,)

        try:
            n_waiting = ser_manager.ser.in_waiting
            if n_waiting:
                parser.feed(ser_manager.ser.read(n_waiting))
        except serial.SerialException:
            print("\nConnection lost.")
            ser_manager.toggle_connection(None, btn)
            return (img,)

        new_columns = parser.pop_columns()
        if not new_columns:
            return (img,)

        for column in new_columns:
            # APPLY DC GUARD: 
            # Zero out the central DC clutter bins so the color map can scale
            # properly around the much weaker motion signatures.
            column[center - DC_GUARD : center + DC_GUARD + 1] = 0

            waterfall = np.roll(waterfall, -1, axis=1)
            waterfall[:, -1] = column
            state["segment_count"] += 1
            
            # Log the original (or masked) data depending on your ML pipeline needs.
            # Here it writes the masked data to keep logs visually consistent.
            if log_fh:
                log_fh.write(" ".join(str(v) for v in column) + "\n")

        if log_fh:
            log_fh.flush()  # once per tick, not once per column -- cheap either way at this rate

        img.set_data(waterfall)
        state["tick"] += 1
        if state["tick"] % 20 == 0:  # periodically re-tighten color scale, not every redraw
            img.set_clim(waterfall.min(), waterfall.max())

        if args.segments and state["segment_count"] >= args.segments:
            print(f"\nCaptured requested {args.segments} columns.")
            state["stop"] = True
            plt.close(fig)

        return (img,)

    anim = FuncAnimation(fig, on_tick, interval=1000.0 / args.redraw_hz,
                         cache_frame_data=False, blit=False)

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        if ser_manager.ser and ser_manager.ser.is_open:
            ser_manager.ser.close()
        if log_fh:
            log_fh.close()
        print(f"Total columns received: {state['segment_count']}")


if __name__ == "__main__":
    main()