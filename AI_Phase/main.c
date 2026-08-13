//******************************************************************************
//  main.c
//
//  MSP430FR5994 radar gesture-recognition firmware.
//
//  Pipeline (hardware-triggered, no CPU polling on the ADC path):
//    Timer_A2 (4 kHz) --hardware trigger--> ADC12_B (A12=IFI, A13=IFQ)
//      --ISR--> ring buffer (I_queue/Q_queue)
//      --main loop, FFT_HOP samples at a time--> STFT_compute_next_segment()
//      --DMA, UART--> host PC (0xC0-marked spectrogram column frames)
//
//  The CPU sleeps in LPM0 whenever there is nothing to do: between ADC
//  triggers, and while the DMA is transmitting a finished column. Only the
//  ADC12_B ISR, the STFT compute call, and the (non-blocking) DMA kickoff
//  run with the CPU actually awake.
//******************************************************************************

#include <driverlib.h>
#include <DSPLib.h>
#include <stdbool.h>
#include "STFT.h"
#include "radar_configuration.h"

// STFT input window -- holds the most recent FFT_SIZE samples, shifted
// left by FFT_HOP each time a new hop's worth of data arrives. Same
// shift-and-append pattern as the original MSP432 main.c.
static uint16_t STFT_input_I[FFT_SIZE];
static uint16_t STFT_input_Q[FFT_SIZE];

// Set true right before UART_putSpectrogramColumn_DMA() kicks off a
// transfer, cleared by the DMA ISR once the last byte has actually left
// the shift register. Read/written from both the main loop and the DMA
// ISR, hence volatile -- this is the sole handshake between the two.
volatile bool dma_tx_in_progress = false;

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
                STFT_input_I[i] = I_queue[samples_index_out];
                STFT_input_Q[i] = Q_queue[samples_index_out];
                samples_index_out = (samples_index_out + 1) % N_SAMPLES;
            }

            // --- Profiling: time the STFT compute itself ---
            uint16_t t0 = TA1R;
            ADC12_B_disableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
            STFT_compute_next_segment(STFT_input_I, STFT_input_Q);
            ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
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
                __bis_SR_register(LPM0_bits + GIE);
            }
            uint16_t t3 = TA1R;
            dma_wait_ticks_accum += (uint16_t)((t3 - t2) & 0x7FFF);
            hop_count_accum++;

            // Kick off the new column's DMA transfer. This call returns
            // immediately (it only arms the DMA and writes the first
            // byte); the transfer itself happens in the background while
            // the CPU goes on to compute the next hop.
            dma_tx_in_progress = true;
            UART_putSpectrogramColumn_DMA(spectrogram[STFT_SEGMENTS - 1], FFT_SIZE);
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
        if (count_snapshot_ready && !dma_tx_in_progress)
        {
            UART_putCountFrame(count_snapshot);
            count_snapshot_ready = false;
        }
        if (profile_snapshot_ready && !dma_tx_in_progress)
        {
            UART_putProfileFrame(hop_count_snapshot, stft_ticks_snapshot, dma_wait_ticks_snapshot);
            profile_snapshot_ready = false;
        }

        __bis_SR_register(LPM0_bits + GIE);   // sleep until ADC ISR wakes us
    }
}

//------------------------------------------------------------------------------
// Interrupt Service Routines
//------------------------------------------------------------------------------

// Timer_A2 CCR0 (4 kHz ADC trigger tick). CONSEQ_1 ("sequence of
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
            __bic_SR_register_on_exit(LPM0_bits); // Wake up CPU for STFT processing
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
    adc_sample_count = 0;
    count_snapshot_ready = true;

    hop_count_snapshot = hop_count_accum;
    stft_ticks_snapshot = stft_ticks_accum;
    dma_wait_ticks_snapshot = dma_wait_ticks_accum;
    hop_count_accum = 0;
    stft_ticks_accum = 0;
    dma_wait_ticks_accum = 0;
    profile_snapshot_ready = true;

    __bic_SR_register_on_exit(LPM0_bits); // Wake CPU to send the snapshots
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
            __bic_SR_register_on_exit(LPM0_bits); // Wake CPU in case it is waiting to send next frame
            break;
        default:
            break;
    }
}