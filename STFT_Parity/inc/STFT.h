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

//******************************************************************************
// Clutter cancellation (fast-time high-pass, single-delay canceller):
// y[n] = x[n] - x[n-1], applied to each NEW raw ADC sample once, before it
// enters the STFT window. Nulls zero-Doppler (static-reflector) content at
// the source instead of relying on windowing/guard-banding to hide it
// after the FFT. See main.c's shift-and-append loop for the actual
// differencing -- it needs the persistent previous-sample state across
// hops, which only exists there.
//
// The parity build deliberately matches the production model-pilot contract.
//******************************************************************************
#define ENABLE_CLUTTER_CANCEL 1

// Left-shift applied to the (much smaller) difference values before they
// enter the Q15 pipeline, replacing the old raw-centering <<4.
//
// Keep synchronized with AI_Phase and the host-side embedded exporter.
#define DIFF_SHIFT 4

extern int8_t spectrogram[STFT_SEGMENTS][FFT_SIZE];

void STFT_init(void);

// stft_input_I/Q are now SIGNED -- either raw-centered values (filter off)
// or difference values (filter on). See main.c.
void STFT_compute_next_segment(int16_t *stft_input_I, int16_t *stft_input_Q);

#endif // STFT_H
