#include <driverlib.h>
#include <stdint.h>
#include "radar_configuration.h"

volatile uint32_t timer_tick_count = 0;

int main(void)
{
    WDT_A_hold(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();
    Init_TIMER();     // ADC intentionally NOT initialized for this test

    __enable_interrupt();

    uint32_t last_sent = 0xFFFFFFFF;   // force the first value through

    while (1)
    {
        uint32_t snapshot = timer_tick_count;   // read once, avoid tearing issues
        if (snapshot != last_sent)
        {
            // Pack the 32-bit counter into the existing 2x16-bit frame format:
            // IFI = low 16 bits, IFQ = high 16 bits
            UART_putFrame((uint16_t)(snapshot & 0xFFFF),
                          (uint16_t)((snapshot >> 16) & 0xFFFF));
            last_sent = snapshot;
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
    timer_tick_count++;
}