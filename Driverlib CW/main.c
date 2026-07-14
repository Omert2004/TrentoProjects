//******************************************************************************
//  MSP430FR5994 - Continuous ADC Sampling of Radar IFI/IFQ (Analog Path)
//
//  PURPOSE:
//    Sample the radar's analog IFI and IFQ outputs continuously with the
//    MCU's own on-chip ADC12_B, and stream the raw 12-bit results out over
//    the REAL backchannel UART (eUSCI_A0, P2.0/P2.1) so a PC serial monitor
//    / matplotlib script can read them from a COM port.
//
//  NOTE: Init_Clock / Init_GPIO / Init_UART / Init_ADC and the UART_put*
//  helpers now live in radar_configuration.c / radar_configuration.h so
//  they can be reused from other source files.
//
//  Hardware Setup:
//    - IFI  -> P3.0 / A12   (radar analog I output)
//    - IFQ  -> P3.1 / A13   (radar analog Q output)
//    - UART -> eUSCI_A0 backchannel (USB debug port), 115200-8-N-1
//              P2.0 = UCA0TXD, P2.1 = UCA0RXD (SECONDARY module function
//              on FR5994 - this differs from some other FR5xx parts)
//
//  Notes:
//    - Uses TI MSP430 DriverLib exclusively (driverlib.h).
//******************************************************************************

#include <driverlib.h>
#include <stdint.h>
#include "radar_configuration.h"

//******************************************************************************
// Main
//******************************************************************************
int main(void)
{
    WDT_A_hold(WDT_A_BASE);

    Init_Clock();
    Init_GPIO();
    Init_UART();
    Init_ADC();

    __enable_interrupt();

    UART_puts("IFI,IFQ\r\n");

    while (1)
    {
        // One pass through the sequence: MEM0 (IFI) then MEM1 (IFQ)
        ADC12_B_startConversion(ADC12_B_BASE, ADC12_B_MEMORY_0,
                                 ADC12_B_SEQOFCHANNELS);

        __bis_SR_register(LPM0_bits + GIE);   // sleep until ISR wakes us

        // Send the parsed results out the real UART pin (P2.0)
        UART_putU16(IFI_result);
        UART_putc(',');
        UART_putU16(IFQ_result);
        UART_puts("\r\n");

        // Rate budget @ 8 MHz / 115200 baud:
        //   - a line like "4095,4095\r\n" is ~11 bytes -> ~0.95 ms to send
        //   - ADC sample+hold+convert for both channels is a few tens of us
        //   - this delay tops the loop up to ~2 ms total -> ~500 sample-pairs/sec
        // Well under the ~1000 lines/sec ceiling 115200 baud allows, leaving
        // headroom. Lower this further (or remove it) to push the rate up,
        // but don't go so low that lines start colliding on the wire.
        __delay_cycles(8000);
    }
}

//******************************************************************************
// ADC12_B ISR
//******************************************************************************
#if defined(__TI_COMPILER_VERSION__) || defined(__IAR_SYSTEMS_ICC__)
#pragma vector = ADC12_VECTOR
__interrupt void ADC12_B_ISR(void)
#elif defined(__GNUC__)
void __attribute__ ((interrupt(ADC12_VECTOR))) ADC12_B_ISR(void)
#else
#error Compiler not supported!
#endif
{
    switch (__even_in_range(ADC12IV, ADC12IV_ADC12RDYIFG))
    {
        case ADC12IV_ADC12IFG1:                        // last channel in sequence (IFQ)
            IFI_result = ADC12_B_getResults(ADC12_B_BASE, ADC12_B_MEMORY_0);
            IFQ_result = ADC12_B_getResults(ADC12_B_BASE, ADC12_B_MEMORY_1);
            __bic_SR_register_on_exit(LPM0_bits);
            break;
        default:
            break;
    }
}