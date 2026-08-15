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


//******************************************************************************
// Integer floor(log2(x)) for positive 32-bit x -- exact replacement for the
// previous (int8_t)log2f((float)magnitude).
//
// Why this is a drop-in, lossless swap and not an approximation: the old
// code immediately truncated log2f()'s float result to int8_t, which is
// exactly floor(log2(x)) for positive x -- the fractional part was already
// being thrown away. floor(log2(x)) is just "the index of the highest set
// bit", computable with a handful of integer compares/shifts instead of a
// software floating-point library call (this core has no FPU, so log2f()
// was doing float normalization + iteration entirely in software -- the
// dominant per-hop cost measured via the 0xC3 profiling frame: ~250-280 ms
// of the ~250-330 ms per column was inside STFT_compute_next_segment(),
// with DMA wait consistently measuring 0 ms).
//
// Binary-search bit-length, 5 compares worst case, all integer ops.
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

//******************************************************************************
// Clamp to the int16_t/Q15 range instead of trusting DIFF_SHIFT (STFT.h)
// to never overflow. DIFF_SHIFT is an empirical value you'll be tuning
// against real hardware -- if it's too aggressive for a given capture,
// this saturates cleanly instead of silently wrapping around (which
// would look like random noise/spikes, exactly the kind of thing that's
// very confusing to debug after the fact).
//******************************************************************************
static inline int16_t clamp_q15(int32_t v)
{
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}


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

void STFT_compute_next_segment(int16_t *stft_input_I, int16_t *stft_input_Q)
{
    int c, r, i, n;
    msp_status status;

    for (c = 0; c < STFT_SEGMENTS - 1; c++)
        for (r = 0; r < FFT_SIZE; r++)
            spectrogram[c][r] = spectrogram[c + 1][r];

    //--------------------------------------------------------------------
    // Step 2: scale into Q15 range.
    //
    // ENABLE_CLUTTER_CANCEL path: stft_input_I/Q already hold signed
    // difference values (main.c) -- a constant offset like the ADC's
    // 2048 midpoint cancels out automatically when you difference two
    // consecutive samples, so the old "- 2048" step would be WRONG here
    // (it would push an already-small diff deeply negative). Just shift,
    // clamping to the Q15 range since DIFF_SHIFT is an empirical value.
    //
    // Fallback path (filter disabled): unchanged from before -- center
    // the raw 12-bit code, then shift.
    //--------------------------------------------------------------------
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
    // plain CPU loop. The log2 step uses ilog2_u32() (integer, see above)
    // rather than log2f() -- measured via the firmware's 0xC3 profiling
    // frame to be the dominant per-hop cost by a wide margin (log2f() is
    // software floating-point on this FPU-less core, called up to 256x
    // per segment). ilog2_u32() produces the identical result, since the
    // old code's (int8_t) cast was already truncating to floor(log2(x)).
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
        int8_t val = (magnitude > 0) ? ilog2_u32((uint32_t)magnitude) : 0;
        // ilog2_u32() only ever returns 0..31 (magnitude is at most ~2^31,
        // see its own comment above), so val is never actually negative
        // here -- this clamp is defensive, not something that can trigger
        // in practice. Kept because it costs nothing and guards against
        // magnitude someday overflowing int32_t if FFT_SIZE/scaling changes.
        spectrogram[STFT_SEGMENTS - 1][nn] = (val < 0) ? 0 : val;
    }
}