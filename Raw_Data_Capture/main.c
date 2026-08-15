//******************************************************************************
//  main.c -- continuous raw I/Q streaming, DMA-driven TX.
//
//  Timer_A2 (SAMPLING_RATE_HZ) --hardware trigger--> ADC12_B (A12=IFI, A13=IFQ)
//    --ISR--> ring buffer (I_queue/Q_queue)
//    --main loop--> UART_putFrame_DMA() (non-blocking, DMA-driven)
//
//  Streams from boot, no host command needed. Frame format (6 bytes, no
//  marker byte): [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi] -- matches
//  raw_print.py / rate_check.py as-is.
//******************************************************************************

#include <driverlib.h>
#include <stdint.h>
#include "radar_configuration.h"

int main(void)
{
    WDT_A_initWatchdogTimer(WDT_A_BASE, WDT_A_CLOCKSOURCE_SMCLK, WDT_A_CLOCKDIVIDER_8192K);
    WDT_A_start(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();
    Init_DMA();
    Init_ADC();
    Init_TIMER();

    __enable_interrupt();

    while (1)
    {
        WDT_A_resetTimer(WDT_A_BASE);

        while (samples_index_out != samples_index_in)
        {
            uint16_t ifi = I_queue[samples_index_out];
            uint16_t ifq = Q_queue[samples_index_out];
            samples_index_out = (samples_index_out + 1) % N_SAMPLES;

            // Wait for the PREVIOUS frame's DMA transfer to finish before
            // overwriting dma_tx_buffer with this one. Sleeping here (not
            // busy-waiting) lets the CPU stay in LPM0 while the UART is
            // still shifting bytes out -- same pattern as the STFT
            // project's column-send wait loop.
            while (dma_tx_in_progress)
            {
                __bis_SR_register(LPM0_bits + GIE);
            }

            dma_tx_in_progress = true;
            UART_putFrame_DMA(ifi, ifq);
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
    // no wake here -- timer only starts a conversion, doesn't itself
    // produce new data yet; waking belongs to the ADC completion ISR below
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
            if (next_in != samples_index_out)   // drop the sample if the ring buffer is full
            {
                I_queue[samples_index_in] = ADC12MEM0;
                Q_queue[samples_index_in] = ADC12MEM1;
                samples_index_in = next_in;
            }
            __bic_SR_register_on_exit(LPM0_bits);   // wake main() to drain buffer
            break;
        }
        default:
            break;
    }
}

// Fires once the DMA has moved the LAST byte of the current frame into
// UCA0TXBUF. Clearing dma_tx_in_progress here is what lets the main
// loop's wait-loop (and the next UART_putFrame_DMA() call) proceed.
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
            dma_tx_in_progress = false;
            __bic_SR_register_on_exit(LPM0_bits);
            break;
        default:
            break;
    }
}