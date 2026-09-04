#ifndef STFT_H
#define STFT_H

#include <stdint.h>

/* STFT geometry used for both training export and on-device inference. */
#define FFT_SIZE 256
#define FFT_HOP 128
#define STFT_SEGMENTS 15

/* First-difference clutter cancellation: y[n] = x[n] - x[n-1]. */
#define ENABLE_CLUTTER_CANCEL 1

/* Q15 scale used by the dataset exporter and deployed model. Changing this
 * value requires regenerating the tensors and retraining the model. */
#define DIFF_SHIFT 4

extern int8_t spectrogram[STFT_SEGMENTS][FFT_SIZE];

void STFT_init(void);

/* Inputs are signed first differences when clutter cancellation is enabled. */
void STFT_compute_next_segment(int16_t *stft_input_I, int16_t *stft_input_Q);

#endif // STFT_H
