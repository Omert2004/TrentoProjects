//******************************************************************************
//  main.c -- continuous raw I/Q streaming, DMA-driven TX.
//
//  Timer_A2 ISR (SAMPLING_RATE_HZ) --ADC12SC--> ADC12_B (A12=IFI, A13=IFQ)
//    --ISR--> ring buffer (I/Q/sample index)
//    --main loop--> 32-sample CRC packets (non-blocking DMA UART)
//
//  Sampling remains exactly 2000 Hz. Packetization removes the per-sample
//  header overhead that made 2000 lossless samples/s impossible at 115200
//  baud with the earlier six-byte frame format.
//******************************************************************************

#include <driverlib.h>
#include <stdint.h>
#include "radar_configuration.h"

static uint16_t packet_i[RAW_PACKET_MAX_SAMPLES];
static uint16_t packet_q[RAW_PACKET_MAX_SAMPLES];
static uint16_t tx_packet_sequence = 0;

static uint16_t samples_available(void)
{
    int in = samples_index_in;
    int out = samples_index_out;
    return (uint16_t)((in >= out) ? (in - out) : (N_SAMPLES - out + in));
}

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

        // Wait until a full packet is available. At 2 kHz this adds only
        // 16 ms of batching latency and amortizes the header/CRC overhead.
        while (samples_available() >= RAW_PACKET_MAX_SAMPLES)
        {
            uint8_t count = 0;
            uint32_t first_sample_index;
            uint32_t drop_snapshot;
            uint16_t packet_sequence;

            WDT_A_resetTimer(WDT_A_BASE);

            // Wait for the previous packet's DMA transfer to finish before
            // overwriting dma_tx_buffer with this one. Sleeping here (not
            // busy-waiting) keeps ADC interrupts active. Feed the watchdog
            // here as a hard guarantee even if a peripheral fault stalls TX.
            while (dma_tx_in_progress)
            {
                WDT_A_resetTimer(WDT_A_BASE);
                __bis_SR_register(LPM0_bits + GIE);
            }

            first_sample_index = sample_index_queue[samples_index_out];
            while (count < RAW_PACKET_MAX_SAMPLES
                   && samples_index_out != samples_index_in)
            {
                uint32_t current_index = sample_index_queue[samples_index_out];

                // Never hide an MCU-side drop inside a packet. End the current
                // packet before a sequence discontinuity; the next packet's
                // first_sample_index exposes the exact gap to the host.
                if (count > 0
                    && current_index != first_sample_index + (uint32_t)count)
                    break;

                packet_i[count] = I_queue[samples_index_out];
                packet_q[count] = Q_queue[samples_index_out];
                samples_index_out = (samples_index_out + 1) % N_SAMPLES;
                count++;
            }

            // The ISR updates this 32-bit value on a 16-bit CPU. Take an
            // interrupt-protected snapshot to prevent a torn read.
            __disable_interrupt();
            drop_snapshot = adc_drop_count;
            __enable_interrupt();

            packet_sequence = tx_packet_sequence++;
            dma_tx_in_progress = true;
            UART_putPacket_DMA(packet_i, packet_q, count, packet_sequence,
                               first_sample_index, drop_snapshot);
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
            uint32_t sample_index = adc_total_sample_count++;
            if (next_in != samples_index_out)
            {
                I_queue[samples_index_in] = ADC12MEM0;
                Q_queue[samples_index_in] = ADC12MEM1;
                sample_index_queue[samples_index_in] = sample_index;
                samples_index_in = next_in;
            }
            else
            {
                adc_drop_count++;
            }
            __bic_SR_register_on_exit(LPM0_bits);   // wake main() to drain buffer
            break;
        }
        default:
            break;
    }
}

// Fires once the DMA has moved the LAST byte of the current packet into
// UCA0TXBUF. Clearing dma_tx_in_progress here is what lets the main
// loop's wait-loop (and the next UART_putPacket_DMA() call) proceed.
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
