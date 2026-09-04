/*
 * Embedded Q15 STFT for the MSP430FR5994 LEA accelerator.
 *
 * The pipeline scales signed IFI/IFQ first differences, interleaves the
 * complex samples, applies a Hann window, performs a fixed-scale 256-point
 * FFT, and stores fftshift(log2(magnitude squared)) as uint5-range int8 data.
 */

#include "STFT.h"
#include "window_q15.h"
#include <DSPLib.h>


/* Integer floor(log2(x)) for positive 32-bit values. */
static inline int8_t ilog2_u32(uint32_t x)
{
    int8_t n = 0;
    if (x >= (1UL << 16)) { n += 16; x >>= 16; }
    if (x >= (1UL << 8))  { n += 8;  x >>= 8;  }
    if (x >= (1UL << 4))  { n += 4;  x >>= 4;  }
    if (x >= (1UL << 2))  { n += 2;  x >>= 2;  }
    if (x >= (1UL << 1))  { n += 1; }
    return n;
}

/* Saturate scaled differences rather than allowing signed wraparound. */
static inline int16_t clamp_q15(int32_t v)
{
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}
/* The 3,840-byte spectrogram lives in FRAM to preserve scarce SRAM. */
#pragma PERSISTENT(spectrogram)
int8_t spectrogram[STFT_SEGMENTS][FFT_SIZE] = {0};

/* Interleaved LEA complex buffer. The alignment is required by DSPLib. */
DSPLIB_DATA(cmplx_buf, MSP_ALIGN_CMPLX_FFT_Q15(FFT_SIZE))
_q15 cmplx_buf[2 * FFT_SIZE];

/* Q15 scaling buffers used before I/Q interleaving. */
DSPLIB_DATA(centered_I, 4)
static _q15 centered_I[FFT_SIZE];

DSPLIB_DATA(centered_Q, 4)
static _q15 centered_Q[FFT_SIZE];

void STFT_init(void)
{
    /* The Hann window is a compile-time constant. */
}

void STFT_compute_next_segment(int16_t *stft_input_I, int16_t *stft_input_Q)
{
    int c, r, i, n;
    msp_status status;

    for (c = 0; c < STFT_SEGMENTS - 1; c++)
        for (r = 0; r < FFT_SIZE; r++)
            spectrogram[c][r] = spectrogram[c + 1][r];

    /* Scale either signed first differences or centered raw ADC samples. */
    for (i = 0; i < FFT_SIZE; i++)
    {
#if ENABLE_CLUTTER_CANCEL
        centered_I[i] = (_q15)clamp_q15((int32_t)stft_input_I[i] << DIFF_SHIFT);
        centered_Q[i] = (_q15)clamp_q15((int32_t)stft_input_Q[i] << DIFF_SHIFT);
#else
        centered_I[i] = (_q15)(((int16_t)stft_input_I[i] - 2048) << 4);
        centered_Q[i] = (_q15)(((int16_t)stft_input_Q[i] - 2048) << 4);
#endif
    }

    /* Interleave I (real) and Q (imaginary). */
    msp_cmplx_q15_params cmplxParams;
    cmplxParams.length = FFT_SIZE;
    status = msp_cmplx_q15(&cmplxParams, centered_I, centered_Q, cmplx_buf);
    msp_checkStatus(status);

    /* Apply the real-valued Hann window to both complex components. */
    msp_cmplx_mpy_real_q15_params mpyParams;
    mpyParams.length = FFT_SIZE;
    status = msp_cmplx_mpy_real_q15(&mpyParams, cmplx_buf, window_q15, cmplx_buf);
    msp_checkStatus(status);

    /* Perform the fixed-scale complex FFT in place. */
    msp_cmplx_fft_q15_params fftParams;
    fftParams.length = FFT_SIZE;
    fftParams.bitReverse = 1;
    fftParams.twiddleTable = 0;   /* Unused by the LEA implementation. */
    status = msp_cmplx_fft_fixed_q15(&fftParams, cmplx_buf);
    msp_checkStatus(status);

    /* Magnitude squared, integer log2, and FFT shift into the output row. */
    for (n = 0; n < FFT_SIZE; n++)
    {
        int16_t re = cmplx_buf[2 * n];
        int16_t im = cmplx_buf[2 * n + 1];
        uint32_t magnitude = (uint32_t)((int32_t)re * re)
                           + (uint32_t)((int32_t)im * im);

        int nn = (n + FFT_SIZE / 2) % FFT_SIZE;
        int8_t val = (magnitude > 0) ? ilog2_u32(magnitude) : 0;
        // ilog2_u32() only ever returns 0..31 (magnitude is at most ~2^31,
        // see its own comment above), so val is never actually negative
        // here -- this clamp is defensive, not something that can trigger
        // in practice. Kept because it costs nothing and guards against
        // magnitude someday overflowing int32_t if FFT_SIZE/scaling changes.
        spectrogram[STFT_SEGMENTS - 1][nn] = (val < 0) ? 0 : val;
    }
}
