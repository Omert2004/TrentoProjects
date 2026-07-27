#ifndef STFT_H
#define STFT_H

#include <stdint.h>

// FFT_SIZE: number of samples per STFT window. Matches the thesis's
// arm_cfft_sR_f32_len256 -- keeping 256 lets us reuse the same frequency
// resolution / time resolution tradeoff that was already validated on
// the PC-side prototype (radar_stft_capture.py).
#define FFT_SIZE 256

// FFT_HOP: how many NEW samples must arrive before computing the next
// spectrogram column. FFT_SIZE/2 = 128 gives 50% overlap between
// consecutive windows, same as the original design.
#define FFT_HOP 128

// STFT_SEGMENTS: how many spectrogram columns (time steps) we keep in the
// rolling image that gets fed to the CNN classifier.
#define STFT_SEGMENTS 15

// The rolling spectrogram image: STFT_SEGMENTS columns x FFT_SIZE
// frequency bins. int8_t because the CNN input in the original thesis
// design expects a small quantized image, not full-precision magnitudes.
extern int8_t spectrogram[STFT_SEGMENTS][FFT_SIZE];

// One-time setup (currently a placeholder -- see STFT.c for what it could
// do if you want to add an LEA revision check later).
void STFT_init(void);

// Call this once you have FFT_SIZE fresh raw ADC samples in stft_input_I/Q
// (after the ring-buffer shift-and-append step in main.c). It:
//   1. Shifts the spectrogram left to make room for a new column
//   2. Centers + windows + FFTs the new window of samples
//   3. Writes the new log-magnitude column into spectrogram[STFT_SEGMENTS-1]
void STFT_compute_next_segment(uint16_t *stft_input_I, uint16_t *stft_input_Q);

#endif // STFT_H