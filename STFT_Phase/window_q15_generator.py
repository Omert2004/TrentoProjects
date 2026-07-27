import numpy as np

# 256 noktalı Hanning penceresi oluştur ve Q15 formatına (maksimum 32767) ölçekle
fft_size = 256
# Hanning formülü: 0.5 * (1 - cos(2*pi*n/N))
hanning_window = np.hanning(fft_size)
window_q15 = np.round(32767 * hanning_window).astype(int)

# C dizisi formatında yazdır
print(f"const int16_t window_q15[{fft_size}] = {{")
for i in range(0, fft_size, 8):
    row = ", ".join(f"{val:6d}" for val in window_q15[i:i+8])
    print(f"    {row},")
print("};")

# Hızlı Kontroller (Sanity Checks)
print(f"\nKontrol 1: Başlangıç değeri 0 mı? -> {window_q15[0]}")
print(f"Kontrol 2: Orta noktalar tepe değerine (32767) yakın mı? -> {window_q15[127]}, {window_q15[128]}")
print(f"Kontrol 3: Simetrik mi? (10. ve 245. elemanlar eşit mi?) -> {window_q15[10]} == {window_q15[255-10]}")