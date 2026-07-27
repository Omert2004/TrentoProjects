import serial
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button

PORT = 'COM7'
BAUD = 115200
BUFFER_SIZE = 200
ADC_MID_SCALE = 2048

ser = None

ifi_data = []
ifq_data = []

fig, ax = plt.subplots()
plt.subplots_adjust(bottom=0.2)

plt.title("Radar I/Q Sinyalleri")
plt.xlabel("Örneklem (Zaman)")
plt.ylabel("ADC Değeri (0-4095)")


def connect_serial(event):
    global ser

    try:
        # If not connected, try to connect
        if ser is None or not ser.is_open:
            ser = serial.Serial(PORT, BAUD, timeout=1)
            button.label.set_text("Disconnect")  # Change label to indicate next action
            print(f"Connected to {PORT}")
            
        # If already connected, disconnect
        else:
            ser.close()
            button.label.set_text("Connect COM") # Revert label
            print(f"Disconnected from {PORT}")

    except serial.SerialException as e:
        print(f"Connection error: {e}")
        button.label.set_text("Failed")


def update(frame):
    global ser

    if ser is not None and ser.is_open:

        while ser.in_waiting:
            raw = ser.readline()

            try:
                line = raw.decode('utf-8', errors='ignore').strip()
            except Exception:
                continue

            if ',' not in line:
                continue

            parts = line.split(',')

            if len(parts) != 2:
                continue

            ifi_str, ifq_str = parts

            if ifi_str.isdigit() and ifq_str.isdigit():
                ifi_data.append(int(ifi_str))
                ifq_data.append(int(ifq_str))


    if len(ifi_data) > BUFFER_SIZE:
        del ifi_data[:-BUFFER_SIZE]
        del ifq_data[:-BUFFER_SIZE]


    ax.clear()
    ax.set_ylim(0, 4100)
    ax.set_title("Radar I/Q Sinyalleri")


    ax.axhline(
        ADC_MID_SCALE,
        color="gray",
        linestyle="--",
        linewidth=1,
        label=f"Mid-scale ({ADC_MID_SCALE})"
    )


    ax.plot(ifi_data, label="IFI (In-Phase)", color="blue")
    ax.plot(ifq_data, label="IFQ (Quadrature)", color="orange")


    if ifi_data and ifq_data:

        stats_text = (
            f"IFI  min={min(ifi_data):4d}  mean={sum(ifi_data)/len(ifi_data):6.1f}  max={max(ifi_data):4d}\n"
            f"IFQ  min={min(ifq_data):4d}  mean={sum(ifq_data)/len(ifq_data):6.1f}  max={max(ifq_data):4d}"
        )


        ax.text(
            0.02,
            0.98,
            stats_text,
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=9,
            family="monospace",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8)
        )


    ax.legend(loc="upper right")


# COM connect button
button_ax = plt.axes([0.4, 0.05, 0.2, 0.075])
button = Button(button_ax, "Connect COM")

button.on_clicked(connect_serial)


ani = FuncAnimation(
    fig,
    update,
    interval=100,
    cache_frame_data=False
)

plt.show()