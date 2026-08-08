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

// Volatile flag to synchronize CPU and DMA
volatile bool dma_tx_in_progress = false;

int main(void)
{
    WDT_A_initWatchdogTimer(WDT_A_BASE, WDT_A_CLOCKSOURCE_SMCLK, WDT_A_CLOCKDIVIDER_8192K);
    WDT_A_start(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();
    Init_DMA();      // Initialize DMA peripheral
    Init_ADC();
    Init_TIMER();
    STFT_init();

    __enable_interrupt();

    while (1)
    {
        WDT_A_resetTimer(WDT_A_BASE);

        // Drain the ring buffer in FFT_HOP-sized chunks, same accounting
        // logic as the original MSP432 main loop.
        int samples = (samples_index_in >= samples_index_out)
                        ? samples_index_in - samples_index_out
                        : N_SAMPLES - samples_index_out + samples_index_in;

        for (; samples >= FFT_HOP; samples -= FFT_HOP)
        {
            // Shift the STFT window left by FFT_HOP, then append the new hop
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

            ADC12_B_disableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
            STFT_compute_next_segment(STFT_input_I, STFT_input_Q);
            ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);

            // Wait in LPM0 if the DMA is still transmitting the previous frame
            while (dma_tx_in_progress)
            {
                __bis_SR_register(LPM0_bits + GIE);
            }

            // Flag DMA as active and fire off the new column
            dma_tx_in_progress = true;
            UART_putSpectrogramColumn_DMA(spectrogram[STFT_SEGMENTS - 1], FFT_SIZE);
        }

        __bis_SR_register(LPM0_bits + GIE);   // sleep until ADC ISR wakes us
    }
}

//------------------------------------------------------------------------------
// Interrupt Service Routines
//------------------------------------------------------------------------------

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
            if (next_in != samples_index_out)
            {
                I_queue[samples_index_in] = ADC12MEM0;
                Q_queue[samples_index_in] = ADC12MEM1;
                samples_index_in = next_in;
            }
            __bic_SR_register_on_exit(LPM0_bits); // Wake up CPU for STFT processing
            break;
        }
        default:
            break;
    }
}

// DMA ISR: Wakes the CPU when UART transmission of the buffer is entirely finished
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