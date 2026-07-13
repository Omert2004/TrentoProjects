import pyserial
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# COM portunu Aygıt Yöneticisindeki MSP UART portuna göre değiştir (örneğin 'COM3' veya 'COM4')
PORT = 'COM7'  
BAUD = 9600

# Seri portu başlat
try:
    ser = pyserial.Serial(PORT, BAUD, timeout=1)
except Exception as e:
    print(f"Port açılamadı: {e}")
    exit()

ifi_data = []
ifq_data = []

# Grafik figürünü oluştur
fig, ax = plt.subplots()
plt.title("Radar I/Q Sinyalleri")
plt.xlabel("Örneklem (Zaman)")
plt.ylabel("ADC Değeri (0-4095)")

def update(frame):
    try:
        # UART'tan bir satır oku ve string'e çevir
        line = ser.readline().decode('utf-8').strip()
        
        # Eğer satırda virgül varsa (doğru format)
        if ',' in line:
            ifi_str, ifq_str = line.split(',')
            
            # Başlıklara denk gelirsek (IFI,IFQ) atla, sadece sayıları al
            if ifi_str.isdigit() or (ifi_str.startswith('-') and ifi_str[1:].isdigit()):
                ifi_data.append(int(ifi_str))
                ifq_data.append(int(ifq_str))

                # Ekranda sadece son 100 veriyi tutalım ki grafik sonsuza kadar sıkışmasın
                ifi_data[:] = ifi_data[-100:]
                ifq_data[:] = ifq_data[-100:]

                # Grafiği temizle ve yeniden çiz
                ax.clear()
                ax.set_ylim(0, 4100) # 12-bit ADC sınırları
                ax.plot(ifi_data, label="IFI (In-Phase)", color="blue")
                ax.plot(ifq_data, label="IFQ (Quadrature)", color="orange")
                ax.legend(loc="upper right")
    except Exception as e:
        pass # Başlangıçtaki bozuk veya eksik verileri yoksay

# Animasyonu başlat (her 50ms'de bir güncellenir)
ani = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
plt.show()