#include <driverlib.h>
#include <stdint.h>
#include "radar_configuration.h"

int main(void)
{
    // Configure Watchdog Timer (3 direct arguments: baseAddress, clockSelect, clockDivider)
    WDT_A_initWatchdogTimer(WDT_A_BASE, WDT_A_CLOCKSOURCE_SMCLK, WDT_A_CLOCKDIVIDER_8192K);
    WDT_A_start(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();
    Init_ADC();             // real ADC now enabled
    Init_TIMER();

    __enable_interrupt();

    while (1)
    {
        // Pet the watchdog timer on each iteration to prevent auto-reset
        WDT_A_resetTimer(WDT_A_BASE);

        // Busy-poll instead of LPM0 -- proven working, LPM0 wake shelved for now.
        while (samples_index_out != samples_index_in)
        {
            uint16_t ifi = I_queue[samples_index_out];
            uint16_t ifq = Q_queue[samples_index_out];
            samples_index_out = (samples_index_out + 1) % N_SAMPLES;

            UART_putFrame(ifi, ifq);
        }
    }
}

#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = TIMER2_A0_VECTOR
__interrupt void TIMER2_A0_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(TIMER2_A0_VECTOR))) TIMER2_A0_ISR(void)
#else
#error Compiler not supported!
#endif
{
    ADC12CTL0 |= ADC12ENC | ADC12SC;   // re-enable AND start each sequence --
                                        // CONSEQ_1 clears ENC after every pass
}

// ADC12_B ISR: fires when MEM1 finishes (end of sequence, since memParam1
// has endOfSequence = ADC12_B_ENDOFSEQUENCE). Pulls both channel results
// into the ring buffer.
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
        case ADC12IV_ADC12IFG1:   // MEM1 conversion complete
        {
            int next_in = (samples_index_in + 1) % N_SAMPLES;
            if (next_in != samples_index_out)
            {
                I_queue[samples_index_in] = ADC12MEM0;
                Q_queue[samples_index_in] = ADC12MEM1;
                samples_index_in = next_in;
            }
            break;
        }
        default:
            break;
    }
}