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

            UART_putFrame(ifi, ifq);
        }

        __bis_SR_register(LPM0_bits + GIE);   // sleep until ADC ISR wakes us
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
            if (next_in != samples_index_out)
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