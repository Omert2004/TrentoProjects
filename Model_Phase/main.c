/*
 * MSP430FR5994 radar gesture-recognition firmware.
 *
 * Timer_A2 triggers the IFI/IFQ ADC sequence at 2 kHz. Accepted samples
 * enter a ring buffer, each 128-sample hop produces one 256-bin STFT column,
 * and 15 complete columns feed the integer 8/16-channel TinyCNN. UART frames
 * D0-D4 provide spectrogram, rate, profile, and classification telemetry.
 */

#include <driverlib.h>
#include <DSPLib.h>
#include <stdbool.h>
#include "STFT.h"
#include "radar_configuration.h"
#include "radar_cnn.h"

/* Rolling, 50%-overlapped STFT inputs. They are signed because clutter
 * cancellation stores first differences rather than raw ADC codes. */
static int16_t STFT_input_I[FFT_SIZE];
static int16_t STFT_input_Q[FFT_SIZE];

/* Handshake between the main loop and the DMA ISR. */
volatile bool dma_tx_in_progress = false;

/* ADC/Timer/DMA interrupts must remain enabled during LEA work so the 2 kHz
 * ring keeps filling, but unrelated ISRs must not terminate DSPLib's LPM0
 * wait. Only the LEA completion interrupt may wake that wait. */
static volatile bool stft_in_progress = false;
static uint16_t tx_column_sequence = 0;
static uint32_t accepted_samples_consumed = 0;
static uint16_t diagnostic_sequence_counter = 0;
static uint16_t inference_sequence_counter = 0;
static uint8_t complete_columns_in_window = 0;
static bool stft_window_primed = false;
static volatile bool acquisition_paused = false;

static void send_pending_diagnostics(void)
{
    bool send_count;
    bool send_profile;
    uint16_t sequence;
    uint16_t count;
    uint16_t hops;
    uint16_t stft_ticks;
    uint16_t dma_ticks;
    uint32_t drops;

    /* Copy and acknowledge one coherent Timer_A1 snapshot quickly. ADC
     * interrupts are disabled only for these memory accesses, never while
     * bytes are shifted over UART. A new snapshot arriving during the sends
     * remains flagged for the next idle slot. */
    __disable_interrupt();
    send_count = count_snapshot_ready;
    send_profile = profile_snapshot_ready;
    sequence = diagnostic_sequence_snapshot;
    count = count_snapshot;
    drops = count_drop_snapshot;
    hops = hop_count_snapshot;
    stft_ticks = stft_ticks_snapshot;
    dma_ticks = dma_wait_ticks_snapshot;
    if (send_count)
        count_snapshot_ready = false;
    if (send_profile)
        profile_snapshot_ready = false;
    __enable_interrupt();

    if (send_count)
    {
        UART_putCountFrame(sequence, count, drops);
    }
    if (send_profile)
    {
        UART_putProfileFrame(sequence, hops, stft_ticks, dma_ticks);
    }
}

#if ENABLE_CLUTTER_CANCEL
// Persists the previous RAW (unfiltered) sample across hops -- this is
// what makes the differencing correct despite the 50%-overlap
// shift-and-append scheme: each unique new sample is touched by this
// exactly once, right where it's first pulled out of the ring buffer.
// Values shifted along by the "shift window left" step below are already
// differenced from a prior hop and must NOT be re-differenced.
static uint16_t last_raw_I = 0;
static uint16_t last_raw_Q = 0;
static bool clutter_canceller_primed = false;
#endif

static void reset_window_after_inference(void)
{
    int i;
    __disable_interrupt();
    samples_index_in = 0;
    samples_index_out = 0;
    __enable_interrupt();
    for (i = 0; i < FFT_SIZE; i++)
    {
        STFT_input_I[i] = 0;
        STFT_input_Q[i] = 0;
    }
#if ENABLE_CLUTTER_CANCEL
    clutter_canceller_primed = false;
#endif
    complete_columns_in_window = 0;
    stft_window_primed = false;
}

int main(void)
{
    WDT_A_initWatchdogTimer(WDT_A_BASE, WDT_A_CLOCKSOURCE_SMCLK, WDT_A_CLOCKDIVIDER_8192K);
    WDT_A_start(WDT_A_BASE);

    // GPIO must come before Clock: Init_Clock() now starts the LFXT
    // crystal for ACLK (see Init_RateTimer()/Test 1), which requires the
    // crystal pins already muxed and LPM5 already unlocked -- both done
    // inside Init_GPIO().
    Init_GPIO();
    Init_Clock();
    Init_UART();
    Init_DMA();             // Initialize DMA peripheral
    Init_ADC();
    Init_TIMER();
    Init_RateTimer();       // Test 1: independent 1 Hz ADC rate counter
    STFT_init();

    __enable_interrupt();

    while (1)
    {
        WDT_A_resetTimer(WDT_A_BASE);

        //----------------------------------------------------------------
        // How many unread samples are sitting in the ring buffer right
        // now? Standard circular-buffer distance calc: if the write
        // pointer (samples_index_in) hasn't wrapped past the read pointer
        // (samples_index_out) since we last checked, the count is just
        // their difference; if it HAS wrapped, add back N_SAMPLES to
        // account for the wrap. Same accounting logic as the original
        // MSP432 main loop.
        //----------------------------------------------------------------
        int samples = (samples_index_in >= samples_index_out)
                        ? samples_index_in - samples_index_out
                        : N_SAMPLES - samples_index_out + samples_index_in;

        // Drain the ring buffer one FFT_HOP chunk at a time -- each full
        // hop's worth of new samples triggers exactly one new spectrogram
        // column.
        for (; samples >= FFT_HOP; samples -= FFT_HOP)
        {
            uint32_t first_new_sample_index = accepted_samples_consumed;
            uint32_t drop_snapshot;
            WDT_A_resetTimer(WDT_A_BASE);
            // Shift the STFT window left by FFT_HOP samples (discarding
            // the oldest hop), then append the new hop at the end. This
            // is what gives consecutive spectrogram columns their 50%
            // overlap (FFT_HOP == FFT_SIZE / 2).
            int i;
            for (i = 0; i < FFT_SIZE - FFT_HOP; i++)
            {
                STFT_input_I[i] = STFT_input_I[i + FFT_HOP];
                STFT_input_Q[i] = STFT_input_Q[i + FFT_HOP];
            }
            for (i = FFT_SIZE - FFT_HOP; i < FFT_SIZE; i++)
            {
                uint16_t raw_I = I_queue[samples_index_out];
                uint16_t raw_Q = Q_queue[samples_index_out];

            #if ENABLE_CLUTTER_CANCEL
                if (!clutter_canceller_primed)
                {
                    // First sample ever: nothing to difference against.
                    // Seed the canceller and emit 0 rather than a bogus
                    // diff against an arbitrary starting value -- this
                    // only affects the very first hop after boot.
                    last_raw_I = raw_I;
                    last_raw_Q = raw_Q;
                    clutter_canceller_primed = true;
                    STFT_input_I[i] = 0;
                    STFT_input_Q[i] = 0;
                }
                else
                {
                    STFT_input_I[i] = (int16_t)raw_I - (int16_t)last_raw_I;
                    STFT_input_Q[i] = (int16_t)raw_Q - (int16_t)last_raw_Q;
                    last_raw_I = raw_I;
                    last_raw_Q = raw_Q;
                }
            #else
                STFT_input_I[i] = (int16_t)raw_I;
                STFT_input_Q[i] = (int16_t)raw_Q;
            #endif

                samples_index_out = (samples_index_out + 1) % N_SAMPLES;
                accepted_samples_consumed++;
            }

            // --- Profiling: time the STFT compute itself ---
            uint16_t t0 = TA1R;
            stft_in_progress = true;
            STFT_compute_next_segment(STFT_input_I, STFT_input_Q);
            stft_in_progress = false;
            uint16_t t1 = TA1R;
            // TA1 runs in up-mode with CCR0 = 32767 (32768 ticks -> wraps
            // at 0x8000). Masking the subtraction to 15 bits gives the
            // correct modular delta even across that wrap -- the standard
            // trick for any free-running counter with a power-of-two
            // period.
            stft_ticks_accum += (uint16_t)((t1 - t0) & 0x7FFF);

            // --- Wait for the PREVIOUS column's DMA transfer to finish
            // before overwriting dma_tx_buffer with the new one. Sleeping
            // here (rather than busy-waiting) is what lets the CPU stay
            // in LPM0 while the UART is still shifting out the last
            // column's bytes. ---
            uint16_t t2 = TA1R;
            while (dma_tx_in_progress)
            {
                WDT_A_resetTimer(WDT_A_BASE);
                __bis_SR_register(LPM0_bits + GIE);
            }
            uint16_t t3 = TA1R;
            dma_wait_ticks_accum += (uint16_t)((t3 - t2) & 0x7FFF);
            hop_count_accum++;

            /* At full 31.25-column/s operation the next column DMA starts
             * immediately, so diagnostics must be inserted in this known-idle
             * slot or they can be starved indefinitely. */
            send_pending_diagnostics();

            /* adc_drop_count is 32-bit on a 16-bit CPU: take an atomic
             * snapshot so the transmitted value can never be torn. */
            __disable_interrupt();
            drop_snapshot = adc_drop_count;
            __enable_interrupt();

            // Kick off the new column's DMA transfer. This call returns
            // immediately (it only arms the DMA and writes the first
            // byte); the transfer itself happens in the background while
            // the CPU goes on to compute the next hop.
            dma_tx_in_progress = true;
            UART_putSpectrogramColumn_DMA(spectrogram[STFT_SEGMENTS - 1],
                                           tx_column_sequence,
                                           first_new_sample_index,
                                           drop_snapshot);

            if (stft_window_primed)
                complete_columns_in_window++;
            else
                stft_window_primed = true;

            if (complete_columns_in_window >= STFT_SEGMENTS)
            {
                int32_t logits[RADAR_CNN_CLASS_COUNT];
                uint8_t prediction;
                acquisition_paused = true;
                Timer_A_stop(TIMER_A2_BASE);
                while (ADC12_B_isBusy(ADC12_B_BASE));
                while (dma_tx_in_progress)
                {
                    WDT_A_resetTimer(WDT_A_BASE);
                    __bis_SR_register(LPM0_bits + GIE);
                }
                /* Inference is a bounded critical section with acquisition stopped.
                 * Hold the watchdog until classification and then restart it. */
                WDT_A_hold(WDT_A_BASE);

                prediction = radar_cnn_classify(&spectrogram[0][0], logits);

                WDT_A_resetTimer(WDT_A_BASE);
                WDT_A_start(WDT_A_BASE);

                UART_putCnnResultFrame(inference_sequence_counter++,
                                    tx_column_sequence, prediction, logits);
                reset_window_after_inference();
                acquisition_paused = false;
                Timer_A_startCounter(TIMER_A2_BASE, TIMER_A_UP_MODE);
                tx_column_sequence++;
                samples = 0;
                break;
            }
            tx_column_sequence++;
        }

        //------------------------------------------------------------
        // Test 1 (1 Hz ADC rate) and profiling (1 Hz STFT/DMA timing)
        // snapshots are sent from the main loop, not their ISRs, and
        // only when dma_tx_in_progress is false. Two reasons:
        //   1. UART_putc() blocks, and blocking inside an ISR is exactly
        //      the mistake this project already hit and fixed elsewhere
        //      (see ADC12_B_startConversion()-in-ISR history).
        //   2. Gating on dma_tx_in_progress guarantees these never write
        //      to UCA0TXBUF while the DMA is mid-transfer on the same
        //      register, which would corrupt both streams.
        //------------------------------------------------------------
        if (!dma_tx_in_progress)
            send_pending_diagnostics();

        __bis_SR_register(LPM0_bits + GIE);   // sleep until ADC ISR wakes us
    }
}

//------------------------------------------------------------------------------
// Interrupt Service Routines
//------------------------------------------------------------------------------

// Timer_A2 CCR0 (2 kHz ADC trigger tick). CONSEQ_1 ("sequence of
// channels") automatically clears ADC12ENC after each completed A12->A13
// sequence, so it must be re-armed here on every tick -- setting it once
// at init is NOT sufficient (this was a confirmed bug during bring-up).
#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = TIMER2_A0_VECTOR
__interrupt void TIMER2_A0_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(TIMER2_A0_VECTOR))) TIMER2_A0_ISR(void)
#else
#error Compiler not supported!
#endif
{
    if (!acquisition_paused)
        ADC12CTL0 |= ADC12ENC | ADC12SC;
}

// Fires once the A12/A13 sequence (IFI, IFQ) has both converted.
#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = ADC12_B_VECTOR
__interrupt void ADC12_B_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(ADC12_B_VECTOR))) ADC12_B_ISR(void)
#else
#error Compiler not supported!
#endif
{
    switch (__even_in_range(ADC12IV, ADC12IV_ADC12IFG31))
    {
        case ADC12IV_ADC12IFG1:
        {
            int next_in = (samples_index_in + 1) % N_SAMPLES;
            if (next_in != samples_index_out)   // drop the sample if the ring buffer is full
            {
                I_queue[samples_index_in] = ADC12MEM0;
                Q_queue[samples_index_in] = ADC12MEM1;
                samples_index_in = next_in;
                adc_sample_count++;   // Test 1: counts only *accepted* samples -- if
                                       // the ring buffer is overflowing, this will read
                                       // LOWER than the true ADC trigger rate, which is
                                       // itself a useful "are we keeping up?" signal.
            }
            else
            {
                adc_drop_count++;
            }
            if (!stft_in_progress)
                __bic_SR_register_on_exit(LPM0_bits);
            break;
        }
        default:
            break;
    }
}

// Timer_A1/ACLK, 1 Hz. Snapshots and zeroes the Test-1 ADC sample counter
// and the STFT/DMA profiling accumulators. Deliberately does NOT touch
// UART here -- UART_putc() blocks, and blocking inside an ISR is exactly
// the mistake already fixed elsewhere in this project. The actual send
// happens in the main loop once *_snapshot_ready is seen.
#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = TIMER1_A0_VECTOR
__interrupt void TIMER1_A0_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(TIMER1_A0_VECTOR))) TIMER1_A0_ISR(void)
#else
#error Compiler not supported!
#endif
{
    count_snapshot = adc_sample_count;
    count_drop_snapshot = adc_drop_count;
    diagnostic_sequence_snapshot = diagnostic_sequence_counter++;
    adc_sample_count = 0;
    count_snapshot_ready = true;

    hop_count_snapshot = hop_count_accum;
    stft_ticks_snapshot = stft_ticks_accum;
    dma_wait_ticks_snapshot = dma_wait_ticks_accum;
    hop_count_accum = 0;
    stft_ticks_accum = 0;
    dma_wait_ticks_accum = 0;
    profile_snapshot_ready = true;

    if (!stft_in_progress)
        __bic_SR_register_on_exit(LPM0_bits);
}

// Fires once the DMA has moved the LAST byte of the current spectrogram
// column into UCA0TXBUF (not when it's finished shifting out on the
// wire -- just when the DMA's own job is done). Clearing
// dma_tx_in_progress here is what lets the main loop's wait-loop (and the
// next UART_putSpectrogramColumn_DMA() call) proceed.
#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = DMA_VECTOR
__interrupt void DMA_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(DMA_VECTOR))) DMA_ISR(void)
#else
#error Compiler not supported!
#endif
{
    switch (__even_in_range(DMAIV, 16))
    {
        case DMAIV_DMA0IFG:
            dma_tx_in_progress = false; // Transmission complete
            if (!stft_in_progress)
                __bic_SR_register_on_exit(LPM0_bits);
            break;
        default:
            break;
    }
}
