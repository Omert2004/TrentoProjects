//******************************************************************************
// STFT.c
//
// Fixed-point (Q15) port of the thesis's STFT_compute_next_segment(), using
// TI's MSP-DSPLib and the FR5994's LEA hardware accelerator instead of
// CMSIS-DSP's arm_cfft_f32() (which is ARM-only and doesn't exist on MSP430).
//
// Pipeline (mirrors the original float version step-for-step):
//   1. Center raw 12-bit ADC codes (0..4095) around zero
//   2. Left-shift into Q15 range for maximum FFT precision
//   3. Interleave I/Q into a single complex buffer (I = real, Q = imaginary
//      -- this is what preserves Doppler direction of motion, same as the
//      thesis's single complex FFT over I+jQ)
//   4. Apply the Hanning window (real-valued, multiplies both I and Q)
//   5. Complex FFT via LEA (msp_cmplx_fft_fixed_q15)
//   6. Magnitude-squared -> log2 -> clamp -> frequency-shift, same as before
//
// Confirmed against TI's official DSPLib docs and example code (not
// guessed) -- see conversation history for the exact sources used.
//******************************************************************************

#include "STFT.h"
#include "window_q15.h"     // Q15 Hanning coefficients
#include <DSPLib.h>
#include <math.h>


//******************************************************************************
// spectrogram moved to FRAM (not SRAM) via #pragma PERSISTENT.
//
// Why: the FR5994 only has 8 KB of SRAM total, and half of that (4 KB) is
// reserved for LEARAM/LEASTACK (needed by the FFT accelerator), leaving
// only ~4 KB of regular RAM for everything else. spectrogram alone is
// 15 * 256 = 3840 bytes -- almost the entire regular RAM budget by itself.
//
// spectrogram is only ever read/written by the CPU (LEA never touches
// it directly), so there's no reason it needs to live in fast SRAM.
// FRAM gives us up to 256 KB of space instead, so moving it here frees
// nearly all of that 3840 bytes back for the ring buffers, FFT scratch
// buffers, and stack.
//
// #pragma PERSISTENT requires an explicit initializer ("= {0}") -- this
// tells the compiler the array should be initialized once, at first
// flash, rather than re-initialized on every reset (the normal behavior
// for .bss variables). For this use case (a rolling buffer that gets
// overwritten every STFT segment anyway) that's exactly the behavior we
// want, and it's required syntax for this pragma either way.
//******************************************************************************
#pragma PERSISTENT(spectrogram)
int8_t spectrogram[STFT_SEGMENTS][FFT_SIZE] = {0};

//******************************************************************************
// LEA-shared buffer for the complex FFT.
//
// Format: interleaved [re0, im0, re1, im1, ...] -- this is the layout
// msp_cmplx_q15() and msp_cmplx_fft_fixed_q15() both expect.
//
// DSPLIB_DATA(name, alignment) places this array into the ".leaRAM" linker
// section (CCS) so the LEA hardware can access it directly. The complex
// FFT specifically requires 4*FFT_SIZE byte alignment -- using anything
// less causes msp_cmplx_fft_fixed_q15() to return MSP_LEA_INVALID_ADDRESS
// instead of actually running.
//
// MSP_ALIGN_CMPLX_FFT_Q15(length) computes that required alignment for us,
// confirmed directly from TI's own example code
// (transform_ex2_cmplx_fft_auto_q15.c), so we don't have to hardcode "4*256".
//******************************************************************************
DSPLIB_DATA(cmplx_buf, MSP_ALIGN_CMPLX_FFT_Q15(FFT_SIZE))
_q15 cmplx_buf[2 * FFT_SIZE];

// Scratch buffers for the centering/shifting step, before interleaving.
// These don't need LEA-shared placement themselves (only the FFT's own
// working buffer does), so they can live in ordinary RAM.
DSPLIB_DATA(centered_I, 4)
static _q15 centered_I[FFT_SIZE];

DSPLIB_DATA(centered_Q, 4)
static _q15 centered_Q[FFT_SIZE];

void STFT_init(void)
{
    // Nothing to precompute at the moment -- the Q15 window table is a
    // compile-time constant (window_q15.c/.h).
    //
    // Optional: TI's docs recommend checking the LEA hardware revision
    // matches what DSPLib was built against, so a silent firmware/silicon
    // mismatch doesn't corrupt results. Left commented out since it needs
    // MSP_LEA_REVISION defined for your exact DSPLib version:
    //
    // if (msp_lea_getRevision() != MSP_LEA_REVISION)
    // {
    //     // handle mismatch -- e.g. blink an error pattern, halt, etc.
    // }
}

void STFT_compute_next_segment(uint16_t *stft_input_I, uint16_t *stft_input_Q)
{
    int c, r, i, n;
    msp_status status;

    //--------------------------------------------------------------------
    // Step 1: shift the spectrogram left by one column to make room for
    // the new one. Identical to the thesis's STFT_compute_next_segment().
    //--------------------------------------------------------------------
    for (c = 0; c < STFT_SEGMENTS - 1; c++)
        for (r = 0; r < FFT_SIZE; r++)
            spectrogram[c][r] = spectrogram[c + 1][r];

    //--------------------------------------------------------------------
    // Step 2: center the raw ADC codes and scale into Q15 range.
    //
    // ADC12_B on this device produces 12-bit unsigned codes: 0..4095.
    // Centering (subtracting 2048, the ADC's midpoint) gives a signed
    // range of -2048..+2047 -- this is the fixed-point equivalent of the
    // thesis's "STFT_input_I[i] - 8192.0f" centering step (their ADC was
    // 14-bit, ours is 12-bit, hence 2048 instead of 8192).
    //
    // Q15 format represents fractional values from -1.0 to ~+1.0 using
    // the full 16-bit signed range (-32768..32767). Our centered value
    // only uses 12 of those bits (11 magnitude bits + sign), so we left-
    // shift by 4 to spread it across nearly the full Q15 range -- this
    // maximizes precision for the FFT and windowing steps that follow.
    // (2047 << 4 = 32752, safely within int16_t range, no overflow.)
    //--------------------------------------------------------------------
    for (i = 0; i < FFT_SIZE; i++)
    {
        centered_I[i] = (_q15)(((int16_t)stft_input_I[i] - 2048) << 4);
        centered_Q[i] = (_q15)(((int16_t)stft_input_Q[i] - 2048) << 4);
    }

    //--------------------------------------------------------------------
    // Step 3: interleave I (real) and Q (imaginary) into the complex FFT
    // buffer. This is the fixed-point equivalent of the thesis's
    // "tbuf[i*2+0] = fi*window[i]; tbuf[i*2+1] = fq*window[i];" packing
    // step -- except we interleave first and window second (see step 4),
    // which is mathematically identical since the window is real-valued
    // and applies equally to both the real and imaginary parts.
    //
    // msp_cmplx_q15() is LEA-accelerated per TI's supported-API table.
    //--------------------------------------------------------------------
    msp_cmplx_q15_params cmplxParams;
    cmplxParams.length = FFT_SIZE;
    status = msp_cmplx_q15(&cmplxParams, centered_I, centered_Q, cmplx_buf);
    msp_checkStatus(status);

    //--------------------------------------------------------------------
    // Step 4: apply the Hanning window.
    //
    // msp_cmplx_mpy_real_q15() multiplies a complex vector by a REAL
    // vector, element by element -- exactly what we need, since the
    // window coefficients are real numbers (no imaginary component),
    // applied identically to both I and Q. This replaces the two
    // separate real multiplies the original design implied.
    //
    // Q15 x Q15 multiplication convention: DSPLib scales the result back
    // down internally so that Q15 x Q15 -> Q15 (not Q30) -- confirmed via
    // TI's own matrix-multiply documentation/forum discussion. So no
    // manual right-shift is needed after this call.
    //--------------------------------------------------------------------
    msp_cmplx_mpy_real_q15_params mpyParams;
    mpyParams.length = FFT_SIZE;
    status = msp_cmplx_mpy_real_q15(&mpyParams, cmplx_buf, window_q15, cmplx_buf);
    msp_checkStatus(status);

    //--------------------------------------------------------------------
    // Step 5: complex FFT, in place, via LEA.
    //
    // msp_cmplx_fft_fixed_q15() applies a fixed 2x scale-down at every
    // FFT stage (as opposed to msp_cmplx_fft_auto_q15(), which picks the
    // scaling dynamically and reports it via an extra output parameter).
    // We use the "fixed" variant deliberately: the scaling factor is
    // then a known constant (2^log2(FFT_SIZE)), which keeps every
    // spectrogram column consistently scaled relative to each other --
    // desirable for a CNN that expects consistent input statistics.
    //
    // twiddleTable = NULL is intentional and correct here, not a
    // placeholder: TI's docs state the twiddle table pointer is unused
    // when the LEA hardware path is active (the FR5994 has LEA), so it
    // can safely be null.
    //--------------------------------------------------------------------
    msp_cmplx_fft_q15_params fftParams;
    fftParams.length = FFT_SIZE;
    fftParams.bitReverse = 1;
    fftParams.twiddleTable = 0;   // ignored on LEA-capable devices (confirmed via TI docs)
    status = msp_cmplx_fft_fixed_q15(&fftParams, cmplx_buf);
    msp_checkStatus(status);

    //--------------------------------------------------------------------
    // Step 6: magnitude-squared -> log2 -> clamp -> frequency-shift.
    //
    // DSPLib has no LEA-accelerated complex-magnitude function (it's
    // absent from TI's own "LEA Supported APIs" table), so this stays a
    // plain CPU loop -- same as the thesis's "r*r + i*i" computation,
    // just reading from the interleaved Q15 buffer instead of a float
    // array. This is cheap relative to the FFT itself.
    //
    // (n + FFT_SIZE/2) % FFT_SIZE reproduces np.fft.fftshift /
    // the thesis's frequency-shift, putting DC in the middle of the row
    // so approach vs. recede Doppler shows up as left vs. right.
    //--------------------------------------------------------------------
    for (n = 0; n < FFT_SIZE; n++)
    {
        int16_t re = cmplx_buf[2 * n];
        int16_t im = cmplx_buf[2 * n + 1];
        int32_t magnitude = (int32_t)re * re + (int32_t)im * im;

        int nn = (n + FFT_SIZE / 2) % FFT_SIZE;
        int8_t val = (magnitude > 0) ? (int8_t)log2f((float)magnitude) : 0;
        spectrogram[STFT_SEGMENTS - 1][nn] = (val < 0) ? 0 : val;
    }
}