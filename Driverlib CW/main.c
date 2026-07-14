//******************************************************************************
//  MSP430FR5994 - Continuous ADC Sampling of Radar IFI/IFQ (Analog Path)
//
//  PURPOSE:
//    Sample the radar's analog IFI and IFQ outputs continuously with the
//    MCU's own on-chip ADC12_B, and stream the raw 12-bit results out over
//    the REAL backchannel UART (eUSCI_A0, P2.0/P2.1) so a PC serial monitor
//    / matplotlib script can read them from a COM port.
//
//  CHANGES IN THIS VERSION:
//    - Added Init_Clock(): DCO now runs at a true 8 MHz instead of the
//      default ~1 MHz. 115200 baud generated from a 1 MHz clock is only
//      ~8.7 clock cycles/bit - too coarse to be reliable, and is almost
//      certainly why values were jumping above 2000 / graph looked glitchy
//      (bit/framing errors corrupting bytes on the wire, not the radar
//      signal itself clipping). Compare: the reference thesis's MSP432 code
//      only reaches clean 115200 baud because ITS SMCLK is 3 MHz.
//    - Fixed Init_UART() to use the correct baud-rate-table values for an
//      8 MHz BRCLK -> 115200 baud, using regular oversampling mode (the
//      previous LOW_FREQUENCY-mode values were for the old 1 MHz clock and
//      were not a valid table entry for that clock/baud combination).
//    - Shortened the per-loop delay so the whole sample->send cycle now
//      runs at roughly 500 sample-pairs/sec (see math in main()), instead
//      of the previous ~20 pairs/sec.
//
//  IMPORTANT: match this to your Python script - set BAUD = 115200 there
//  too. A baud mismatch between MCU and PC is exactly what produces
//  garbled/oversized values and a "frozen" plot while pyserial waits on
//  malformed lines.
//
//  Hardware Setup:
//    - IFI  -> P3.0 / A12   (radar analog I output)
//    - IFQ  -> P3.1 / A13   (radar analog Q output)
//    - UART -> eUSCI_A0 backchannel (USB debug port), now 115200-8-N-1
//              P2.0 = UCA0TXD, P2.1 = UCA0RXD (SECONDARY module function
//              on FR5994 - this differs from some other FR5xx parts)
//
//  Notes:
//    - Uses TI MSP430 DriverLib exclusively (driverlib.h).
//******************************************************************************

#include <driverlib.h>
#include <stdint.h>

//******************************************************************************
// Globals
//******************************************************************************
volatile uint16_t IFI_result = 0;
volatile uint16_t IFQ_result = 0;

//******************************************************************************
// Minimal UART helpers (blocking, no printf/retargeting needed)
//******************************************************************************
static void UART_putc(uint8_t c)
{
    while (!(UCA0IFG & UCTXIFG));
    UCA0TXBUF = c;
}

static void UART_puts(const char *s)
{
    while (*s) UART_putc((uint8_t)*s++);
}

// Writes a 16-bit unsigned value as decimal ASCII (max 4095 for 12-bit ADC)
static void UART_putU16(uint16_t val)
{
    char buf[6];
    int8_t i = 5;
    buf[5] = '\0';

    if (val == 0)
    {
        UART_putc('0');
        return;
    }
    while (val > 0 && i > 0)
    {
        i--;
        buf[i] = (char)('0' + (val % 10));
        val /= 10;
    }
    UART_puts(&buf[i]);
}

//******************************************************************************
// Init Functions
//******************************************************************************
void Init_Clock(void)
{
    // Bump DCO to a true 8 MHz so 115200 baud can be generated accurately.
    // FRAM needs 0 wait states up to 8 MHz, so no FRCTL change is needed.
    CS_setDCOFreq(CS_DCORSEL_1, CS_DCOFSEL_3);           // DCO = 8 MHz
    CS_initClockSignal(CS_MCLK,  CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
    CS_initClockSignal(CS_SMCLK, CS_DCOCLK_SELECT, CS_CLOCK_DIVIDER_1);
}

void Init_GPIO(void)
{
    // ADC input pins: P3.0 = A12 (IFI), P3.1 = A13 (IFQ)
    // Analog function on FR5xx/6xx requires both PxSEL1 and PxSEL0 set,
    // which DriverLib exposes as GPIO_TERNARY_MODULE_FUNCTION.
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P3, GPIO_PIN0 | GPIO_PIN1, GPIO_TERNARY_MODULE_FUNCTION);

    // UART pins: P2.0 = UCA0TXD, P2.1 = UCA0RXD (LaunchPad backchannel).
    // On the FR5994, UART on P2.0/P2.1 is the SECONDARY module function.
    GPIO_setAsPeripheralModuleFunctionInputPin(
        GPIO_PORT_P2, GPIO_PIN0 | GPIO_PIN1, GPIO_SECONDARY_MODULE_FUNCTION);

    // Unlock GPIO after ALL pin configuration is done (required on FR5xx/6xx)
    PMM_unlockLPM5();
}

void Init_UART(void)
{
    // Standard TI baud-rate-table entry for an 8 MHz BRCLK -> 115200 baud
    // (clockPrescalar=4, firstModReg(UCBRFx)=5, secondModReg(UCBRSx)=0x55,
    // regular oversampling mode - 8MHz/115200 = 69.4 clocks/bit, comfortably
    // inside the accurate range, unlike the old 1MHz/115200 combination).
    EUSCI_A_UART_initParam uartConfig = {0};
    uartConfig.selectClockSource = EUSCI_A_UART_CLOCKSOURCE_SMCLK;
    uartConfig.clockPrescalar    = 4;
    uartConfig.firstModReg       = 5;
    uartConfig.secondModReg      = 0x55;
    uartConfig.parity            = EUSCI_A_UART_NO_PARITY;
    uartConfig.msborLsbFirst     = EUSCI_A_UART_LSB_FIRST;
    uartConfig.numberofStopBits  = EUSCI_A_UART_ONE_STOP_BIT;
    uartConfig.uartMode          = EUSCI_A_UART_MODE;
    uartConfig.overSampling      = EUSCI_A_UART_OVERSAMPLING_BAUDRATE_GENERATION;

    EUSCI_A_UART_init(EUSCI_A0_BASE, &uartConfig);
    EUSCI_A_UART_enable(EUSCI_A0_BASE);
}

void Init_ADC(void)
{
    // ADC12CLK = ADC12OSC (internal ~5 MHz osc, always available regardless
    // of what MCLK/SMCLK happen to be set to elsewhere in the project)
    ADC12_B_initParam adcConfig = {0};
    adcConfig.sampleHoldSignalSourceSelect = ADC12_B_SAMPLEHOLDSOURCE_SC;
    adcConfig.clockSourceSelect            = ADC12_B_CLOCKSOURCE_ADC12OSC;
    adcConfig.clockSourceDivider           = ADC12_B_CLOCKDIVIDER_1;
    adcConfig.clockSourcePredivider        = ADC12_B_CLOCKPREDIVIDER__1;
    adcConfig.internalChannelMap           = ADC12_B_NOINTCH;

    ADC12_B_init(ADC12_B_BASE, &adcConfig);
    ADC12_B_enable(ADC12_B_BASE);

    // Longer sample-and-hold gives the radar's analog output stage time to settle
    ADC12_B_setupSamplingTimer(ADC12_B_BASE,
        ADC12_B_CYCLEHOLD_128_CYCLES, ADC12_B_CYCLEHOLD_128_CYCLES,
        ADC12_B_MULTIPLESAMPLESENABLE);

    // MEM0 = IFI (A12)
    ADC12_B_configureMemoryParam memParam0 = {0};
    memParam0.memoryBufferControlIndex = ADC12_B_MEMORY_0;
    memParam0.inputSourceSelect        = ADC12_B_INPUT_A12;
    memParam0.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam0.endOfSequence            = ADC12_B_NOTENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam0);

    // MEM1 = IFQ (A13), end of sequence
    ADC12_B_configureMemoryParam memParam1 = {0};
    memParam1.memoryBufferControlIndex = ADC12_B_MEMORY_1;
    memParam1.inputSourceSelect        = ADC12_B_INPUT_A13;
    memParam1.refVoltageSourceSelect   = ADC12_B_VREFPOS_AVCC_VREFNEG_VSS;
    memParam1.endOfSequence            = ADC12_B_ENDOFSEQUENCE;
    ADC12_B_configureMemory(ADC12_B_BASE, &memParam1);

    ADC12_B_clearInterrupt(ADC12_B_BASE, 0, ADC12_B_IFG1);
    ADC12_B_enableInterrupt(ADC12_B_BASE, ADC12_B_IE1, 0, 0);
}

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